"""Strict MCP content selection and provider-to-public DTO projection."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
import re
from typing import Any, Callable, TypeVar

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ValidationError

from app.baidu_map.errors import BaiduMapProtocolError, BaiduMapResponseError
from app.baidu_map.models import (
    AddressComponent,
    BaiduCoordinate,
    CurrentWeather,
    DailyWeatherForecast,
    DirectionsNoResult,
    DirectionsRequest,
    DirectionsSuccess,
    GeocodeCandidate,
    GeocodeRequest,
    GeocodeSuccess,
    MapCapability,
    MapProvenance,
    MatrixTravelMode,
    PlaceDetails,
    PlaceDetailsRequest,
    PlaceDetailsSuccess,
    PlaceSearchNoResult,
    PlaceSearchRequest,
    PlaceSearchSuccess,
    PlaceSummary,
    ReverseGeocodeRequest,
    ReverseGeocodeSuccess,
    RouteAlternative,
    RouteMatrixCellNoResult,
    RouteMatrixCellSuccess,
    RouteMatrixNoResult,
    RouteMatrixRequest,
    RouteMatrixSuccess,
    RouteStep,
    TravelMode,
    WeatherRequest,
    WeatherSuccess,
)


_MAX_SELECTED_JSON_BYTES = 1024 * 1024
_ASCII_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", re.ASCII)
_ASCII_ADCODE = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_T = TypeVar("_T", bound=BaseModel)


def _response_error(capability: MapCapability) -> BaiduMapResponseError:
    return BaiduMapResponseError(capability=capability)


def _object(value: object, capability: MapCapability) -> dict[str, Any]:
    if type(value) is not dict:
        raise _response_error(capability)
    return value


def _array(value: object, capability: MapCapability) -> list[Any]:
    if type(value) is not list:
        raise _response_error(capability)
    return value


def _strict_int(value: object, capability: MapCapability, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _response_error(capability)
    return value


def _number(
    value: object,
    capability: MapCapability,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_decimal_string: bool = False,
) -> float:
    failed = False
    parsed = math.nan
    if type(value) in (int, float):
        try:
            parsed = float(value)
        except (OverflowError, ValueError):
            failed = True
    elif allow_decimal_string and type(value) is str and _ASCII_DECIMAL.fullmatch(value):
        try:
            parsed = float(Decimal(value))
        except (InvalidOperation, OverflowError):
            failed = True
    else:
        raise _response_error(capability)
    if failed or not math.isfinite(parsed):
        raise _response_error(capability)
    if minimum is not None and parsed < minimum:
        raise _response_error(capability)
    if maximum is not None and parsed > maximum:
        raise _response_error(capability)
    return parsed


def _required_text(value: object, capability: MapCapability, *, maximum: int = 512) -> str:
    if type(value) is not str:
        raise _response_error(capability)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise _response_error(capability)
    return cleaned


def _optional_text(value: object, capability: MapCapability, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise _response_error(capability)
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise _response_error(capability)
    return cleaned


def _coordinate(
    value: object,
    capability: MapCapability,
    *,
    allow_decimal_string: bool = False,
) -> BaiduCoordinate:
    raw = _object(value, capability)
    latitude = _number(
        raw.get("lat"), capability, minimum=-90, maximum=90, allow_decimal_string=allow_decimal_string
    )
    longitude = _number(
        raw.get("lng"), capability, minimum=-180, maximum=180, allow_decimal_string=allow_decimal_string
    )
    return _construct(BaiduCoordinate, capability, latitude=latitude, longitude=longitude)


def _provenance(capability: MapCapability, retrieved_at: datetime) -> MapProvenance:
    return _construct(
        MapProvenance,
        capability,
        capability=capability,
        retrieved_at=retrieved_at,
        verified=True,
    )


def _construct(model: type[_T], error_capability: MapCapability, **values: object) -> _T:
    failed = False
    result: _T | None = None
    try:
        result = model(**values)
    except (ValidationError, ValueError, TypeError):
        failed = True
    if failed or result is None:
        raise _response_error(error_capability)
    return result


def select_response_object(result: CallToolResult, capability: MapCapability) -> dict[str, Any]:
    """Apply isError, structuredContent and single-full-JSON-text precedence."""
    if type(result) is not CallToolResult:
        raise BaiduMapProtocolError(capability=capability)
    if result.isError is True:
        raise BaiduMapProtocolError(capability=capability)
    selected: object
    if result.structuredContent is not None:
        content = result.content
        if type(content) is not list or any(type(block) is not TextContent for block in content):
            raise BaiduMapProtocolError(capability=capability)
        selected = result.structuredContent
        if type(selected) is not dict:
            raise BaiduMapProtocolError(capability=capability)
    else:
        content = result.content
        if type(content) is not list or len(content) != 1 or type(content[0]) is not TextContent:
            raise BaiduMapProtocolError(capability=capability)
        failed = False
        selected = None
        try:
            selected = json.loads(content[0].text)
        except (json.JSONDecodeError, TypeError, ValueError):
            failed = True
        if failed or type(selected) is not dict:
            raise BaiduMapProtocolError(capability=capability)
    def json_tree(value: object) -> bool:
        if value is None or type(value) in (str, int, float, bool):
            return not (type(value) is float and not math.isfinite(value))
        if type(value) is list:
            return all(json_tree(item) for item in value)
        if type(value) is dict:
            return all(type(key) is str and json_tree(item) for key, item in value.items())
        return False

    failed = not json_tree(selected)
    encoded_size = 0
    try:
        encoded_size = len(
            json.dumps(selected, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        failed = True
    if failed or encoded_size > _MAX_SELECTED_JSON_BYTES:
        raise BaiduMapResponseError(capability=capability)
    return selected


def _status_root(raw: object, capability: MapCapability) -> dict[str, Any]:
    root = _object(raw, capability)
    status = root.get("status")
    if type(status) is not int:
        raise _response_error(capability)
    if status != 0:
        raise _response_error(capability)
    return root


def parse_geocode(raw: object, request: GeocodeRequest, retrieved_at: datetime) -> GeocodeSuccess:
    capability = MapCapability.GEOCODING
    root = _status_root(raw, capability)
    result = _object(root.get("result"), capability)
    if not result:
        raise _response_error(capability)
    coordinate = _coordinate(result.get("location"), capability)
    confidence: float | None = None
    if "confidence" in result and result["confidence"] is not None:
        value = result["confidence"]
        if type(value) is not int or not 0 <= value <= 100:
            raise _response_error(capability)
        confidence = value / 100
    candidate = _construct(
        GeocodeCandidate,
        capability,
        formatted_address=request.address,
        coordinate=coordinate,
        confidence=confidence,
    )
    return _construct(
        GeocodeSuccess, capability, result=candidate, provenance=_provenance(capability, retrieved_at)
    )


def _adcode(value: object, capability: MapCapability) -> int | None:
    if value is None:
        return None
    if type(value) is int:
        parsed = value
    elif type(value) is str and _ASCII_ADCODE.fullmatch(value):
        parsed = int(value, 10)
    else:
        raise _response_error(capability)
    if not 0 <= parsed <= 999999:
        raise _response_error(capability)
    return parsed


def _place_summary(raw_value: object, capability: MapCapability) -> PlaceSummary | None:
    raw = _object(raw_value, capability)
    incomplete = False
    uid: str | None = None
    name: str | None = None
    address: str | None = None
    for field in ("uid", "name", "address"):
        if field not in raw or raw[field] is None:
            incomplete = True
        elif field == "uid":
            uid = _required_text(raw[field], capability)
        elif field == "name":
            name = _required_text(raw[field], capability)
        else:
            address = _required_text(raw[field], capability)

    latitude: float | None = None
    longitude: float | None = None
    location = raw.get("location")
    if location is None:
        incomplete = True
    else:
        if type(location) is not dict:
            raise _response_error(capability)
        if "lat" not in location or location["lat"] is None:
            incomplete = True
        else:
            latitude = _number(location["lat"], capability, minimum=-90, maximum=90)
        if "lng" not in location or location["lng"] is None:
            incomplete = True
        else:
            longitude = _number(location["lng"], capability, minimum=-180, maximum=180)
    category = _optional_text(raw.get("tag"), capability)
    if incomplete:
        return None
    assert uid is not None and name is not None and address is not None
    assert latitude is not None and longitude is not None
    return _construct(
        PlaceSummary,
        capability,
        uid=uid,
        name=name,
        address=address,
        coordinate=_construct(
            BaiduCoordinate,
            capability,
            latitude=latitude,
            longitude=longitude,
        ),
        category=category,
    )


def _reverse_place_summary(
    raw_value: object, capability: MapCapability
) -> PlaceSummary | None:
    raw = _object(raw_value, capability)
    incomplete = False
    texts: dict[str, str] = {}
    for name in ("uid", "name", "addr"):
        if name not in raw or raw[name] is None:
            incomplete = True
        else:
            texts[name] = _required_text(raw[name], capability)
    latitude: float | None = None
    longitude: float | None = None
    if "point" not in raw or raw["point"] is None:
        incomplete = True
    else:
        point = raw["point"]
        if type(point) is not dict:
            raise _response_error(capability)
        for name in ("x", "y"):
            if name not in point or point[name] is None:
                incomplete = True
        if "x" in point and point["x"] is not None:
            longitude = _number(point["x"], capability, minimum=-180, maximum=180)
        if "y" in point and point["y"] is not None:
            latitude = _number(point["y"], capability, minimum=-90, maximum=90)
    category = _optional_text(raw.get("tag"), capability)
    if incomplete:
        return None
    assert latitude is not None and longitude is not None
    coordinate = _construct(
        BaiduCoordinate,
        capability,
        latitude=latitude,
        longitude=longitude,
    )
    return _construct(
        PlaceSummary,
        capability,
        uid=texts["uid"],
        name=texts["name"],
        address=texts["addr"],
        coordinate=coordinate,
        category=category,
    )


def parse_reverse_geocode(
    raw: object, request: ReverseGeocodeRequest, retrieved_at: datetime
) -> ReverseGeocodeSuccess:
    del request
    capability = MapCapability.REVERSE_GEOCODING
    root = _status_root(raw, capability)
    result = _object(root.get("result"), capability)
    if not result:
        raise _response_error(capability)
    component = _object(result.get("addressComponent"), capability)
    address_component = _construct(
        AddressComponent,
        capability,
        country=_optional_text(component.get("country"), capability),
        province=_optional_text(component.get("province"), capability),
        city=_optional_text(component.get("city"), capability),
        district=_optional_text(component.get("district"), capability),
        town=_optional_text(component.get("town"), capability),
        adcode=_adcode(component.get("adcode"), capability),
    )
    pois = _array(result.get("pois", []), capability)
    # Validate every provider item before the explicit public 20-item projection.
    qualified = tuple(
        item
        for item in (_reverse_place_summary(item, capability) for item in pois)
        if item is not None
    )
    nearby = qualified[:20]
    return _construct(
        ReverseGeocodeSuccess,
        capability,
        formatted_address=_required_text(result.get("formatted_address"), capability),
        address_component=address_component,
        nearby_places=nearby,
        provenance=_provenance(capability, retrieved_at),
    )


def parse_place_search(
    raw: object, request: PlaceSearchRequest, retrieved_at: datetime
) -> PlaceSearchSuccess | PlaceSearchNoResult:
    capability = MapCapability.PLACE_SEARCH
    root = _status_root(raw, capability)
    results = _array(root.get("results"), capability)
    if len(results) > 50:
        raise _response_error(capability)
    places = tuple(
        item for item in (_place_summary(item, capability) for item in results) if item is not None
    )
    provenance = _provenance(capability, retrieved_at)
    if not places:
        return _construct(PlaceSearchNoResult, capability, provenance=provenance)
    if request.result_limit is not None:
        places = places[: request.result_limit]
    return _construct(PlaceSearchSuccess, capability, places=places, provenance=provenance)


def parse_place_details(
    raw: object, request: PlaceDetailsRequest, retrieved_at: datetime
) -> PlaceDetailsSuccess:
    capability = MapCapability.PLACE_DETAILS
    root = _status_root(raw, capability)
    result = _object(root.get("result"), capability)
    if not result:
        raise _response_error(capability)
    uid = _required_text(result.get("uid"), capability)
    if uid != request.uid:
        raise _response_error(capability)
    detail = result.get("detail_info", {})
    detail = _object(detail, capability)
    rating = None
    if "overall_rating" in detail and detail["overall_rating"] is not None:
        rating = _number(
            detail["overall_rating"], capability, minimum=0, maximum=5, allow_decimal_string=True
        )
    shop_hours = _optional_text(detail.get("shop_hours"), capability)
    details = _construct(
        PlaceDetails,
        capability,
        uid=uid,
        name=_required_text(result.get("name"), capability),
        address=_required_text(result.get("address"), capability),
        coordinate=_coordinate(result.get("location"), capability),
        category=_optional_text(detail.get("tag"), capability),
        brand=_optional_text(detail.get("brand"), capability),
        telephone=_optional_text(result.get("telephone"), capability),
        rating=rating,
        business_hours=(shop_hours,) if shop_hours is not None else (),
    )
    return _construct(
        PlaceDetailsSuccess, capability, details=details, provenance=_provenance(capability, retrieved_at)
    )


def parse_route_matrix(
    raw: object, request: RouteMatrixRequest, retrieved_at: datetime
) -> RouteMatrixSuccess | RouteMatrixNoResult:
    capability = MapCapability.ROUTE_MATRIX
    root = _status_root(raw, capability)
    results = _array(root.get("result"), capability)
    expected = len(request.origins) * len(request.destinations)
    provenance = _provenance(capability, retrieved_at)
    if not results:
        return _construct(RouteMatrixNoResult, capability, provenance=provenance)
    if len(results) != expected:
        raise _response_error(capability)
    cells: list[RouteMatrixCellSuccess | RouteMatrixCellNoResult] = []
    success_count = 0
    for flat_index, raw_cell in enumerate(results):
        cell = _object(raw_cell, capability)
        distance_raw = cell.get("distance")
        duration_raw = cell.get("duration")
        distance_present = "distance" in cell
        duration_present = "duration" in cell
        if distance_present and type(distance_raw) is not dict:
            raise _response_error(capability)
        if duration_present and type(duration_raw) is not dict:
            raise _response_error(capability)
        distance = distance_raw if distance_present else None
        duration = duration_raw if duration_present else None
        distance_value_present = distance_present and "value" in distance
        duration_value_present = duration_present and "value" in duration
        if distance_value_present != duration_value_present:
            raise _response_error(capability)
        origin_index = flat_index // len(request.destinations)
        destination_index = flat_index % len(request.destinations)
        if not distance_value_present:
            cells.append(
                _construct(
                    RouteMatrixCellNoResult,
                    capability,
                    origin_index=origin_index,
                    destination_index=destination_index,
                )
            )
            continue
        success_count += 1
        cells.append(
            _construct(
                RouteMatrixCellSuccess,
                capability,
                origin_index=origin_index,
                destination_index=destination_index,
                distance_m=_strict_int(distance.get("value"), capability),
                duration_s=_strict_int(duration.get("value"), capability),
            )
        )
    if success_count == 0:
        return _construct(RouteMatrixNoResult, capability, provenance=provenance)
    return _construct(
        RouteMatrixSuccess,
        capability,
        origin_count=len(request.origins),
        destination_count=len(request.destinations),
        cells=tuple(cells),
        provenance=provenance,
    )


def _route_step(raw_value: object, capability: MapCapability, *, allow_blank: bool) -> RouteStep | None:
    raw = _object(raw_value, capability)
    instruction_value = raw.get("instruction")
    if type(instruction_value) is not str or len(instruction_value) > 1024:
        raise _response_error(capability)
    instruction = instruction_value.strip()
    if not instruction:
        if allow_blank:
            # Validate every remaining raw field before deterministically filtering it.
            _strict_int(raw.get("distance"), capability)
            _strict_int(raw.get("duration"), capability)
            _coordinate(raw.get("start_location"), capability, allow_decimal_string=True)
            _coordinate(raw.get("end_location"), capability, allow_decimal_string=True)
            return None
        raise _response_error(capability)
    return _construct(
        RouteStep,
        capability,
        instruction=instruction,
        distance_m=_strict_int(raw.get("distance"), capability),
        duration_s=_strict_int(raw.get("duration"), capability),
        start=_coordinate(raw.get("start_location"), capability, allow_decimal_string=True),
        end=_coordinate(raw.get("end_location"), capability, allow_decimal_string=True),
    )


def parse_directions(
    raw: object, request: DirectionsRequest, retrieved_at: datetime
) -> DirectionsSuccess | DirectionsNoResult:
    capability = MapCapability.DIRECTIONS
    root = _status_root(raw, capability)
    result = _object(root.get("result"), capability)
    routes = _array(result.get("routes"), capability)
    provenance = _provenance(capability, retrieved_at)
    if not routes:
        if set(root) != {"status", "result"} or set(result) != {"routes"}:
            raise _response_error(capability)
        return _construct(DirectionsNoResult, capability, provenance=provenance)
    if len(routes) > 3:
        raise _response_error(capability)
    alternatives: list[RouteAlternative] = []
    for raw_route in routes:
        route = _object(raw_route, capability)
        raw_steps = _array(route.get("steps"), capability)
        flattened: list[object]
        if request.mode is TravelMode.TRANSIT:
            flattened = []
            if not raw_steps or any(type(group) is not list for group in raw_steps):
                raise _response_error(capability)
            for group in raw_steps:
                if not group or any(type(item) is not dict for item in group):
                    raise _response_error(capability)
                flattened.extend(group)
        else:
            if any(type(item) is not dict for item in raw_steps):
                raise _response_error(capability)
            flattened = list(raw_steps)
        if not 1 <= len(flattened) <= 128:
            raise _response_error(capability)
        steps = tuple(
            step
            for step in (
                _route_step(item, capability, allow_blank=request.mode is TravelMode.TRANSIT)
                for item in flattened
            )
            if step is not None
        )
        if not 1 <= len(steps) <= 128:
            raise _response_error(capability)
        alternatives.append(
            _construct(
                RouteAlternative,
                capability,
                distance_m=_strict_int(route.get("distance"), capability),
                duration_s=_strict_int(route.get("duration"), capability),
                steps=steps,
            )
        )
    return _construct(
        DirectionsSuccess,
        capability,
        alternatives=tuple(alternatives[: request.alternatives_limit]),
        provenance=provenance,
    )


def parse_weather(raw: object, request: WeatherRequest, retrieved_at: datetime) -> WeatherSuccess:
    del request
    capability = MapCapability.WEATHER
    root = _status_root(raw, capability)
    result = _object(root.get("result"), capability)
    if not result:
        raise _response_error(capability)
    now = _object(result.get("now"), capability)
    current = _construct(
        CurrentWeather,
        capability,
        condition=_required_text(now.get("text"), capability),
        temperature_c=_number(now.get("temp"), capability, allow_decimal_string=True),
        feels_like_c=(
            None
            if now.get("feels_like") is None
            else _number(now["feels_like"], capability, allow_decimal_string=True)
        ),
        humidity_percent=(
            None
            if now.get("rh") is None
            else _number(
                now["rh"], capability, minimum=0, maximum=100, allow_decimal_string=True
            )
        ),
        wind_description=_optional_text(now.get("wind_class"), capability),
    )
    raw_forecasts = _array(result.get("forecasts"), capability)
    if len(raw_forecasts) > 7:
        raise _response_error(capability)
    forecasts: list[DailyWeatherForecast] = []
    last_date: date | None = None
    for raw_forecast in raw_forecasts:
        forecast = _object(raw_forecast, capability)
        raw_date = forecast.get("date")
        parsed_date: date | None = None
        if type(raw_date) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date, re.ASCII):
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError:
                parsed_date = None
        if parsed_date is None or (last_date is not None and parsed_date <= last_date):
            raise _response_error(capability)
        last_date = parsed_date
        forecasts.append(
            _construct(
                DailyWeatherForecast,
                capability,
                date=parsed_date,
                day_condition=_required_text(forecast.get("text_day"), capability),
                night_condition=_required_text(forecast.get("text_night"), capability),
                high_c=_number(forecast.get("high"), capability, allow_decimal_string=True),
                low_c=_number(forecast.get("low"), capability, allow_decimal_string=True),
            )
        )
    return _construct(
        WeatherSuccess,
        capability,
        current=current,
        forecast=tuple(forecasts[:5]),
        provenance=_provenance(capability, retrieved_at),
    )


Parser = Callable[[object, object, datetime], object]


__all__ = (
    "parse_directions",
    "parse_geocode",
    "parse_place_details",
    "parse_place_search",
    "parse_reverse_geocode",
    "parse_route_matrix",
    "parse_weather",
    "select_response_object",
)
