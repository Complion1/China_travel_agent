"""Systematic unit coverage for the complete T01 public model contract."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.baidu_map import (
    AddressComponent,
    BaiduCoordinate,
    BaiduMapAuthenticationError,
    BaiduMapConcurrencyError,
    BaiduMapConfigurationError,
    BaiduMapError,
    BaiduMapErrorCode,
    BaiduMapLimits,
    BaiduMapProtocolError,
    BaiduMapRateLimitError,
    BaiduMapRequestError,
    BaiduMapResponseError,
    BaiduMapSessionError,
    BaiduMapTimeoutError,
    BaiduMapToolSchemaError,
    BaiduMapToolUnavailableError,
    BaiduMapTransportError,
    CitySearchArea,
    CoordinateSystem,
    CurrentWeather,
    DailyWeatherForecast,
    DirectionsNoResult,
    DirectionsOutcome,
    DirectionsRequest,
    DirectionsSuccess,
    GeocodeAmbiguous,
    GeocodeCandidate,
    GeocodeNoResult,
    GeocodeOutcome,
    GeocodeRequest,
    GeocodeSuccess,
    MapCapability,
    MapCapabilityAvailability,
    MapCapabilityUnavailableReason,
    MapLaunchNoResult,
    MapLaunchOutcome,
    MapLaunchRequest,
    MapLaunchRouteRequest,
    MapLaunchSearchRequest,
    MapLaunchService,
    MapLaunchSuccess,
    MapOutcomeStatus,
    MapProvenance,
    MatrixTravelMode,
    NearbySearchArea,
    PlaceDetails,
    PlaceDetailsNoResult,
    PlaceDetailsOutcome,
    PlaceDetailsRequest,
    PlaceDetailsSuccess,
    PlaceSearchArea,
    PlaceSearchAreaType,
    PlaceSearchNoResult,
    PlaceSearchOutcome,
    PlaceSearchRequest,
    PlaceSearchSuccess,
    PlaceSummary,
    ReverseGeocodeNoResult,
    ReverseGeocodeOutcome,
    ReverseGeocodeRequest,
    ReverseGeocodeSuccess,
    RouteAlternative,
    RouteEndpoint,
    RouteMatrixCell,
    RouteMatrixCellNoResult,
    RouteMatrixCellSuccess,
    RouteMatrixNoResult,
    RouteMatrixOutcome,
    RouteMatrixRequest,
    RouteMatrixSuccess,
    RouteStep,
    TravelMode,
    WeatherNoResult,
    WeatherOutcome,
    WeatherRequest,
    WeatherSuccess,
)
from app.baidu_map.models.common import _clean_text_tuple
from app.baidu_map.models.common import _clean_optional_text
from app.baidu_map.models.map_launch import _validate_safe_map_url


def coordinate(latitude: float = 39.9, longitude: float = 116.4) -> BaiduCoordinate:
    return BaiduCoordinate(latitude=latitude, longitude=longitude)


def provenance(capability: MapCapability) -> MapProvenance:
    return MapProvenance(
        capability=capability,
        retrieved_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        verified=True,
    )


def _instances() -> tuple[tuple[type[BaseModel], BaseModel], ...]:
    point = coordinate()
    place = PlaceSummary(uid="u1", name="地点", address="地址", coordinate=point, category="景点")
    details = PlaceDetails(uid="u1", name="地点", address="地址", coordinate=point, category="景点", brand="品牌", telephone="010-12345678", rating=4.5, business_hours=("09:00-18:00",))
    endpoint = RouteEndpoint(coordinate=point, label="起点")
    step = RouteStep(instruction="直行", distance_m=10, duration_s=20, start=point, end=coordinate(40, 117))
    alternative = RouteAlternative(distance_m=10, duration_s=20, steps=(step,))
    matrix_cell = RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=10, duration_s=20)
    forecast = DailyWeatherForecast(date=date(2026, 8, 20), day_condition="晴", night_condition="晴", high_c=30.0, low_c=20.0)
    return (
        (BaiduCoordinate, point),
        (MapProvenance, provenance(MapCapability.GEOCODING)),
        (GeocodeRequest, GeocodeRequest(address="北京站", city="北京")),
        (GeocodeCandidate, GeocodeCandidate(formatted_address="北京站", coordinate=point, confidence=0.9)),
        (GeocodeSuccess, GeocodeSuccess(result=GeocodeCandidate(formatted_address="北京站", coordinate=point), provenance=provenance(MapCapability.GEOCODING))),
        (GeocodeNoResult, GeocodeNoResult(provenance=provenance(MapCapability.GEOCODING))),
        (GeocodeAmbiguous, GeocodeAmbiguous(candidates=(GeocodeCandidate(formatted_address="甲", coordinate=point), GeocodeCandidate(formatted_address="乙", coordinate=coordinate(40, 117))), provenance=provenance(MapCapability.GEOCODING))),
        (ReverseGeocodeRequest, ReverseGeocodeRequest(location=point)),
        (AddressComponent, AddressComponent(country="中国", adcode=110000)),
        (ReverseGeocodeSuccess, ReverseGeocodeSuccess(formatted_address="北京", address_component=AddressComponent(city="北京"), nearby_places=(place,), provenance=provenance(MapCapability.REVERSE_GEOCODING))),
        (ReverseGeocodeNoResult, ReverseGeocodeNoResult(provenance=provenance(MapCapability.REVERSE_GEOCODING))),
        (PlaceSummary, place),
        (PlaceDetails, details),
        (CitySearchArea, CitySearchArea(region="北京")),
        (NearbySearchArea, NearbySearchArea(center=point, radius_m=100)),
        (PlaceSearchRequest, PlaceSearchRequest(query="公园", area=CitySearchArea(region="北京"), result_limit=10)),
        (PlaceSearchSuccess, PlaceSearchSuccess(places=(place,), provenance=provenance(MapCapability.PLACE_SEARCH))),
        (PlaceSearchNoResult, PlaceSearchNoResult(provenance=provenance(MapCapability.PLACE_SEARCH))),
        (PlaceDetailsRequest, PlaceDetailsRequest(uid="u1")),
        (PlaceDetailsSuccess, PlaceDetailsSuccess(details=details, provenance=provenance(MapCapability.PLACE_DETAILS))),
        (PlaceDetailsNoResult, PlaceDetailsNoResult(provenance=provenance(MapCapability.PLACE_DETAILS))),
        (RouteMatrixRequest, RouteMatrixRequest(mode=MatrixTravelMode.DRIVING, origins=(point,), destinations=(coordinate(40, 117),))),
        (RouteMatrixCellSuccess, matrix_cell),
        (RouteMatrixCellNoResult, RouteMatrixCellNoResult(origin_index=0, destination_index=0)),
        (RouteMatrixSuccess, RouteMatrixSuccess(origin_count=1, destination_count=1, cells=(matrix_cell,), provenance=provenance(MapCapability.ROUTE_MATRIX))),
        (RouteMatrixNoResult, RouteMatrixNoResult(provenance=provenance(MapCapability.ROUTE_MATRIX))),
        (RouteEndpoint, endpoint),
        (DirectionsRequest, DirectionsRequest(mode=TravelMode.DRIVING, origin=endpoint, destination=RouteEndpoint(coordinate=coordinate(40, 117)))),
        (RouteStep, step),
        (RouteAlternative, alternative),
        (DirectionsSuccess, DirectionsSuccess(alternatives=(alternative,), provenance=provenance(MapCapability.DIRECTIONS))),
        (DirectionsNoResult, DirectionsNoResult(provenance=provenance(MapCapability.DIRECTIONS))),
        (WeatherRequest, WeatherRequest(location=point)),
        (CurrentWeather, CurrentWeather(condition="晴", temperature_c=25.0, humidity_percent=40.0)),
        (DailyWeatherForecast, forecast),
        (WeatherSuccess, WeatherSuccess(current=CurrentWeather(condition="晴", temperature_c=25.0), forecast=(forecast,), provenance=provenance(MapCapability.WEATHER))),
        (WeatherNoResult, WeatherNoResult(provenance=provenance(MapCapability.WEATHER))),
        (MapLaunchRouteRequest, MapLaunchRouteRequest(origin=endpoint, destination=RouteEndpoint(coordinate=coordinate(40, 117)), mode=TravelMode.DRIVING)),
        (MapLaunchSearchRequest, MapLaunchSearchRequest(query="公园", location=point, radius_m=100)),
        (MapLaunchSuccess, MapLaunchSuccess(url="https://map.baidu.com/search?query=park", provenance=provenance(MapCapability.MAP_LAUNCH))),
        (MapLaunchNoResult, MapLaunchNoResult(provenance=provenance(MapCapability.MAP_LAUNCH))),
        (MapCapabilityAvailability, MapCapabilityAvailability(capability=MapCapability.MAP_LAUNCH, available=False, reason_code=MapCapabilityUnavailableReason.TOOL_NOT_LISTED)),
    )


EXPECTED_FIELDS = {
    BaiduCoordinate: ("schema_version", "coordinate_system", "latitude", "longitude"),
    MapProvenance: ("schema_version", "source", "capability", "retrieved_at", "verified"),
    GeocodeRequest: ("schema_version", "address", "city"),
    GeocodeCandidate: ("schema_version", "formatted_address", "coordinate", "confidence"),
    GeocodeSuccess: ("schema_version", "status", "result", "provenance"),
    GeocodeNoResult: ("schema_version", "status", "provenance"),
    GeocodeAmbiguous: ("schema_version", "status", "candidates", "provenance"),
    ReverseGeocodeRequest: ("schema_version", "location"),
    AddressComponent: ("schema_version", "country", "province", "city", "district", "town", "adcode"),
    ReverseGeocodeSuccess: ("schema_version", "status", "formatted_address", "address_component", "nearby_places", "provenance"),
    ReverseGeocodeNoResult: ("schema_version", "status", "provenance"),
    PlaceSummary: ("schema_version", "uid", "name", "address", "coordinate", "category"),
    PlaceDetails: ("schema_version", "uid", "name", "address", "coordinate", "category", "brand", "telephone", "rating", "business_hours"),
    CitySearchArea: ("schema_version", "area_type", "region"),
    NearbySearchArea: ("schema_version", "area_type", "center", "radius_m"),
    PlaceSearchRequest: ("schema_version", "query", "tag", "area", "result_limit"),
    PlaceSearchSuccess: ("schema_version", "status", "places", "provenance"),
    PlaceSearchNoResult: ("schema_version", "status", "provenance"),
    PlaceDetailsRequest: ("schema_version", "uid"),
    PlaceDetailsSuccess: ("schema_version", "status", "details", "provenance"),
    PlaceDetailsNoResult: ("schema_version", "status", "provenance"),
    RouteMatrixRequest: ("schema_version", "mode", "origins", "destinations"),
    RouteMatrixCellSuccess: ("schema_version", "status", "origin_index", "destination_index", "distance_m", "duration_s"),
    RouteMatrixCellNoResult: ("schema_version", "status", "origin_index", "destination_index"),
    RouteMatrixSuccess: ("schema_version", "status", "origin_count", "destination_count", "cells", "provenance"),
    RouteMatrixNoResult: ("schema_version", "status", "provenance"),
    RouteEndpoint: ("schema_version", "coordinate", "label"),
    DirectionsRequest: ("schema_version", "mode", "origin", "destination", "alternatives_limit"),
    RouteStep: ("schema_version", "instruction", "distance_m", "duration_s", "start", "end"),
    RouteAlternative: ("schema_version", "distance_m", "duration_s", "steps"),
    DirectionsSuccess: ("schema_version", "status", "alternatives", "provenance"),
    DirectionsNoResult: ("schema_version", "status", "provenance"),
    WeatherRequest: ("schema_version", "location"),
    CurrentWeather: ("schema_version", "condition", "temperature_c", "feels_like_c", "humidity_percent", "wind_description"),
    DailyWeatherForecast: ("schema_version", "date", "day_condition", "night_condition", "high_c", "low_c"),
    WeatherSuccess: ("schema_version", "status", "current", "forecast", "provenance"),
    WeatherNoResult: ("schema_version", "status", "provenance"),
    MapLaunchRouteRequest: ("schema_version", "service", "origin", "destination", "mode", "region"),
    MapLaunchSearchRequest: ("schema_version", "service", "query", "region", "location", "radius_m"),
    MapLaunchSuccess: ("schema_version", "status", "url", "provenance"),
    MapLaunchNoResult: ("schema_version", "status", "provenance"),
    MapCapabilityAvailability: ("schema_version", "capability", "available", "reason_code"),
}


def test_all_public_dtos_have_exact_fields_and_strict_immutable_config() -> None:
    instances = _instances()
    assert {model for model, _ in instances} == set(EXPECTED_FIELDS)
    for model, instance in instances:
        assert tuple(model.model_fields) == EXPECTED_FIELDS[model]
        assert tuple(model.model_fields)[0] == "schema_version"
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"
        with pytest.raises(ValidationError):
            setattr(instance, "schema_version", "1.0")
        payload = instance.model_dump(mode="python")
        payload["unexpected"] = True
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_all_public_dtos_json_round_trip_to_fresh_nested_values() -> None:
    for model, instance in _instances():
        clone = model.model_validate_json(instance.model_dump_json())
        assert clone == instance
        assert clone is not instance
        nested = tuple(value for value in instance.__dict__.values() if isinstance(value, BaseModel))
        clone_nested = tuple(value for value in clone.__dict__.values() if isinstance(value, BaseModel))
        assert len(nested) == len(clone_nested)
        assert all(left is not right for left, right in zip(nested, clone_nested))


def test_all_public_dtos_fail_closed_for_schema_versions_in_python_and_json() -> None:
    for model, instance in _instances():
        for invalid_version in (" 1.0 ", "1.1"):
            python_payload = instance.model_dump(mode="python")
            python_payload["schema_version"] = invalid_version
            with pytest.raises(ValidationError):
                model.model_validate(python_payload)

            json_payload = instance.model_dump(mode="json")
            json_payload["schema_version"] = invalid_version
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(json_payload, ensure_ascii=False))


def test_all_fixed_semantic_fields_fail_closed_for_whitespace_and_unknown_values() -> None:
    fixed_fields = {
        "coordinate_system",
        "source",
        "capability",
        "status",
        "area_type",
        "service",
        "mode",
        "reason_code",
    }
    for model, instance in _instances():
        for field_name in fixed_fields.intersection(model.model_fields):
            python_payload = instance.model_dump(mode="python")
            json_payload = instance.model_dump(mode="json")
            valid_value = str(json_payload[field_name])
            for invalid_value in (f" {valid_value} ", "unknown"):
                python_invalid = dict(python_payload)
                python_invalid[field_name] = invalid_value
                with pytest.raises(ValidationError):
                    model.model_validate(python_invalid)

                json_invalid = dict(json_payload)
                json_invalid[field_name] = invalid_value
                with pytest.raises(ValidationError):
                    model.model_validate_json(json.dumps(json_invalid, ensure_ascii=False))


def test_all_public_enums_have_exact_members_and_values() -> None:
    expected = {
        CoordinateSystem: {"BD09LL": "bd09ll"},
        MapCapability: {"GEOCODING": "geocoding", "REVERSE_GEOCODING": "reverse_geocoding", "PLACE_SEARCH": "place_search", "PLACE_DETAILS": "place_details", "ROUTE_MATRIX": "route_matrix", "DIRECTIONS": "directions", "WEATHER": "weather", "MAP_LAUNCH": "map_launch"},
        TravelMode: {"DRIVING": "driving", "RIDING": "riding", "WALKING": "walking", "TRANSIT": "transit"},
        MatrixTravelMode: {"DRIVING": "driving", "RIDING": "riding", "WALKING": "walking"},
        MapOutcomeStatus: {"SUCCESS": "success", "NO_RESULT": "no_result", "AMBIGUOUS": "ambiguous"},
        PlaceSearchAreaType: {"CITY": "city", "NEARBY": "nearby"},
        MapLaunchService: {"ROUTE": "route", "SEARCH": "search"},
        MapCapabilityUnavailableReason: {"NOT_IN_MANIFEST": "not_in_manifest", "TOOL_NOT_LISTED": "tool_not_listed", "SCHEMA_INCOMPATIBLE": "schema_incompatible"},
    }
    for enum_type, members in expected.items():
        assert {member.name: member.value for member in enum_type} == members


def test_all_closed_unions_accept_every_branch_and_reject_unknown_discriminators() -> None:
    point = coordinate()
    place = PlaceSummary(uid="u1", name="地点", address="地址", coordinate=point)
    route_endpoint = RouteEndpoint(coordinate=point)
    step = RouteStep(instruction="直行", distance_m=1, duration_s=1, start=point, end=point)
    alternative = RouteAlternative(distance_m=1, duration_s=1, steps=(step,))
    matrix_success = RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=1, duration_s=1)
    branches = (
        (GeocodeOutcome, (GeocodeSuccess(result=GeocodeCandidate(formatted_address="甲", coordinate=point), provenance=provenance(MapCapability.GEOCODING)), GeocodeNoResult(provenance=provenance(MapCapability.GEOCODING)), GeocodeAmbiguous(candidates=(GeocodeCandidate(formatted_address="甲", coordinate=point), GeocodeCandidate(formatted_address="乙", coordinate=point)), provenance=provenance(MapCapability.GEOCODING))), "status"),
        (ReverseGeocodeOutcome, (ReverseGeocodeSuccess(formatted_address="北京", address_component=AddressComponent(), provenance=provenance(MapCapability.REVERSE_GEOCODING)), ReverseGeocodeNoResult(provenance=provenance(MapCapability.REVERSE_GEOCODING))), "status"),
        (PlaceSearchArea, (CitySearchArea(region="北京"), NearbySearchArea(center=point, radius_m=1)), "area_type"),
        (PlaceSearchOutcome, (PlaceSearchSuccess(places=(place,), provenance=provenance(MapCapability.PLACE_SEARCH)), PlaceSearchNoResult(provenance=provenance(MapCapability.PLACE_SEARCH))), "status"),
        (PlaceDetailsOutcome, (PlaceDetailsSuccess(details=PlaceDetails(uid="u1", name="地点", address="地址", coordinate=point), provenance=provenance(MapCapability.PLACE_DETAILS)), PlaceDetailsNoResult(provenance=provenance(MapCapability.PLACE_DETAILS))), "status"),
        (RouteMatrixCell, (matrix_success, RouteMatrixCellNoResult(origin_index=0, destination_index=0)), "status"),
        (RouteMatrixOutcome, (RouteMatrixSuccess(origin_count=1, destination_count=1, cells=(matrix_success,), provenance=provenance(MapCapability.ROUTE_MATRIX)), RouteMatrixNoResult(provenance=provenance(MapCapability.ROUTE_MATRIX))), "status"),
        (DirectionsOutcome, (DirectionsSuccess(alternatives=(alternative,), provenance=provenance(MapCapability.DIRECTIONS)), DirectionsNoResult(provenance=provenance(MapCapability.DIRECTIONS))), "status"),
        (WeatherOutcome, (WeatherSuccess(current=CurrentWeather(condition="晴", temperature_c=20), provenance=provenance(MapCapability.WEATHER)), WeatherNoResult(provenance=provenance(MapCapability.WEATHER))), "status"),
        (MapLaunchRequest, (MapLaunchRouteRequest(origin=route_endpoint, destination=route_endpoint, mode=TravelMode.DRIVING), MapLaunchSearchRequest(query="公园")), "service"),
        (MapLaunchOutcome, (MapLaunchSuccess(url="https://map.baidu.com/search?query=park", provenance=provenance(MapCapability.MAP_LAUNCH)), MapLaunchNoResult(provenance=provenance(MapCapability.MAP_LAUNCH))), "status"),
    )
    for union_type, values, discriminator in branches:
        adapter = TypeAdapter(union_type)
        for value in values:
            assert type(adapter.validate_python(value)) is type(value)
            assert type(adapter.validate_json(value.model_dump_json())) is type(value)
            invalid = value.model_dump(mode="json")
            invalid[discriminator] = "unknown"
            with pytest.raises(ValidationError):
                adapter.validate_python(invalid)


def test_frozen_boundaries_and_cross_field_invariants() -> None:
    point = coordinate()
    with pytest.raises(ValidationError):
        GeocodeRequest(address="x" * 513)
    with pytest.raises(ValidationError):
        RouteStep(instruction="x" * 1025, distance_m=1, duration_s=1, start=point, end=point)
    with pytest.raises(ValidationError):
        BaiduCoordinate(latitude=True, longitude=116.4)
    with pytest.raises(ValidationError):
        BaiduCoordinate(latitude=math.inf, longitude=116.4)
    with pytest.raises(ValidationError):
        BaiduCoordinate(latitude=-91, longitude=116.4)
    with pytest.raises(ValidationError):
        GeocodeCandidate(formatted_address="甲", coordinate=point, confidence=1.1)
    with pytest.raises(ValidationError):
        PlaceDetails(uid="u", name="n", address="a", coordinate=point, rating=5.1)
    with pytest.raises(ValidationError):
        CurrentWeather(condition="晴", temperature_c=20, humidity_percent=100.1)
    with pytest.raises(ValidationError):
        NearbySearchArea(center=point, radius_m=50001)
    with pytest.raises(ValidationError):
        PlaceSearchRequest(query="公园", area=CitySearchArea(region="北京"), result_limit=51)
    candidate = GeocodeCandidate(formatted_address="甲", coordinate=point)
    with pytest.raises(ValidationError):
        GeocodeAmbiguous(candidates=(candidate,), provenance=provenance(MapCapability.GEOCODING))
    with pytest.raises(ValidationError):
        GeocodeAmbiguous(candidates=tuple(candidate for _ in range(11)), provenance=provenance(MapCapability.GEOCODING))
    place = PlaceSummary(uid="u", name="n", address="a", coordinate=point)
    with pytest.raises(ValidationError):
        PlaceSearchSuccess(places=(), provenance=provenance(MapCapability.PLACE_SEARCH))
    with pytest.raises(ValidationError):
        ReverseGeocodeSuccess(formatted_address="北京", address_component=AddressComponent(), nearby_places=tuple(place for _ in range(21)), provenance=provenance(MapCapability.REVERSE_GEOCODING))
    with pytest.raises(ValidationError):
        PlaceDetails(uid="u", name="n", address="a", coordinate=point, business_hours=tuple("09:00" for _ in range(17)))
    with pytest.raises(ValidationError):
        RouteMatrixRequest(mode=MatrixTravelMode.DRIVING, origins=tuple(point for _ in range(11)), destinations=tuple(point for _ in range(10)))
    good_cell = RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=1, duration_s=1)
    with pytest.raises(ValidationError):
        RouteMatrixSuccess(origin_count=1, destination_count=2, cells=(good_cell, good_cell), provenance=provenance(MapCapability.ROUTE_MATRIX))
    with pytest.raises(ValidationError):
        RouteMatrixSuccess(origin_count=1, destination_count=1, cells=(RouteMatrixCellNoResult(origin_index=0, destination_index=0),), provenance=provenance(MapCapability.ROUTE_MATRIX))
    step = RouteStep(instruction="直行", distance_m=1, duration_s=1, start=point, end=point)
    with pytest.raises(ValidationError):
        RouteAlternative(distance_m=1, duration_s=1, steps=tuple(step for _ in range(129)))
    alternative = RouteAlternative(distance_m=1, duration_s=1, steps=(step,))
    with pytest.raises(ValidationError):
        DirectionsSuccess(alternatives=tuple(alternative for _ in range(4)), provenance=provenance(MapCapability.DIRECTIONS))
    with pytest.raises(ValidationError):
        DailyWeatherForecast(date=date(2026, 8, 20), day_condition="晴", night_condition="晴", high_c=1, low_c=2)
    forecast = DailyWeatherForecast(date=date(2026, 8, 20), day_condition="晴", night_condition="晴", high_c=2, low_c=1)
    with pytest.raises(ValidationError):
        WeatherSuccess(current=CurrentWeather(condition="晴", temperature_c=20), forecast=(forecast, forecast), provenance=provenance(MapCapability.WEATHER))
    with pytest.raises(ValidationError):
        MapLaunchSearchRequest(query="公园", location=point)
    with pytest.raises(ValidationError):
        MapCapabilityAvailability(capability=MapCapability.MAP_LAUNCH, available=True, reason_code=MapCapabilityUnavailableReason.TOOL_NOT_LISTED)
    with pytest.raises(ValidationError):
        MapCapabilityAvailability(capability=MapCapability.GEOCODING, available=False, reason_code=MapCapabilityUnavailableReason.TOOL_NOT_LISTED)
    for url in ("http://map.baidu.com/x", "https://user:pass@map.baidu.com/x", "https://map.baidu.com:444/x", "https://evil.example/x", "https://map.baidu.com/x?token=x", "https://map.baidu.com/x?a%6b=x", "https://map.baidu.com./x", "https://map.baidu.com/" + "x" * 2048):
        with pytest.raises(ValidationError):
            MapLaunchSuccess(url=url, provenance=provenance(MapCapability.MAP_LAUNCH))


def test_all_errors_are_frozen_zero_sensitive_and_one_to_one_with_codes() -> None:
    pairs = (
        (BaiduMapRequestError, BaiduMapErrorCode.REQUEST_ERROR),
        (BaiduMapConfigurationError, BaiduMapErrorCode.CONFIGURATION_ERROR),
        (BaiduMapAuthenticationError, BaiduMapErrorCode.AUTHENTICATION_ERROR),
        (BaiduMapRateLimitError, BaiduMapErrorCode.RATE_LIMIT_ERROR),
        (BaiduMapTimeoutError, BaiduMapErrorCode.TIMEOUT_ERROR),
        (BaiduMapTransportError, BaiduMapErrorCode.TRANSPORT_ERROR),
        (BaiduMapSessionError, BaiduMapErrorCode.SESSION_ERROR),
        (BaiduMapToolUnavailableError, BaiduMapErrorCode.TOOL_UNAVAILABLE),
        (BaiduMapToolSchemaError, BaiduMapErrorCode.TOOL_SCHEMA_ERROR),
        (BaiduMapProtocolError, BaiduMapErrorCode.PROTOCOL_ERROR),
        (BaiduMapResponseError, BaiduMapErrorCode.RESPONSE_ERROR),
        (BaiduMapConcurrencyError, BaiduMapErrorCode.CONCURRENCY_ERROR),
    )
    assert BaiduMapError.__slots__ == ("_capability",)
    assert {code for _, code in pairs} == set(BaiduMapErrorCode)
    assert all(error_type.__slots__ == () for error_type, _ in pairs)
    for state_name in ("_sealed", "_locked", "_frozen", "_initialized"):
        assert not hasattr(BaiduMapError, state_name)
    assert len({code for _, code in pairs}) == len(pairs)
    for error_type, code in pairs:
        assert error_type.code is code
        assert vars(error_type)["code"] is code
        for capability in (None, MapCapability.WEATHER):
            error = error_type(capability=capability)
            capability_text = capability.value if capability else "none"
            assert error.capability is capability
            assert getattr(error, "_capability") is capability
            assert not hasattr(error, "_sealed")
            assert not hasattr(error, "_locked")
            assert not hasattr(error, "_frozen")
            assert not hasattr(error, "_initialized")
            assert error.args == (code.value, capability_text)
            assert str(error) == f"{error_type.__name__}(code={code.value}, capability={capability_text})"
            assert repr(error) == str(error)
            assert all(
                secret_or_payload not in repr(error)
                for secret_or_payload in ("fake-secret", "https://", "payload=", "uid=", "BAIDU_MAPS_API_KEY")
            )
            assert not hasattr(error, "__dict__")
            try:
                raise error
            except error_type as caught:
                assert caught is error
                assert caught.__traceback__ is not None
                traceback = caught.__traceback__
                caught.__traceback__ = traceback
                assert caught.__traceback__ is traceback
                assert caught.code is code
                assert caught.capability is capability
                assert caught.args == (code.value, capability_text)
                assert str(caught) == str(error)
                assert repr(caught) == repr(error)
            for attribute, value in (("code", code), ("capability", capability), ("_capability", capability), ("args", ("bad",))):
                with pytest.raises(AttributeError):
                    setattr(error, attribute, value)
                with pytest.raises(AttributeError):
                    delattr(error, attribute)
            with pytest.raises(AttributeError):
                error.extra = "forbidden"
            with pytest.raises(AttributeError):
                del error.extra


def test_limits_have_all_frozen_defaults_and_every_boundary() -> None:
    defaults = {
        "connect_timeout_s": 10.0,
        "write_timeout_s": 10.0,
        "pool_timeout_s": 10.0,
        "initialize_timeout_s": 15.0,
        "list_tools_timeout_s": 15.0,
        "geocode_timeout_s": 15.0,
        "reverse_geocode_timeout_s": 15.0,
        "place_search_timeout_s": 20.0,
        "place_details_timeout_s": 15.0,
        "route_matrix_timeout_s": 30.0,
        "directions_timeout_s": 30.0,
        "weather_timeout_s": 15.0,
        "map_launch_timeout_s": 20.0,
        "close_timeout_s": 5.0,
        "max_in_flight": 3,
    }
    assert tuple(BaiduMapLimits.model_fields) == tuple(defaults)
    assert BaiduMapLimits().model_dump() == defaults
    assert BaiduMapLimits() == BaiduMapLimits(**defaults)
    assert BaiduMapLimits(max_in_flight=1).max_in_flight == 1
    assert BaiduMapLimits(max_in_flight=3).max_in_flight == 3
    for field, default in defaults.items():
        if field == "max_in_flight":
            for invalid in (0, 4, True):
                with pytest.raises(ValidationError):
                    BaiduMapLimits(**{field: invalid})
            continue
        for invalid in (0, -1, math.nan, math.inf, default + 0.1):
            with pytest.raises(ValidationError):
                BaiduMapLimits(**{field: invalid})
        assert getattr(BaiduMapLimits(**{field: default / 2}), field) == default / 2
    with pytest.raises(ValidationError):
        BaiduMapLimits(extra="x")


def test_model_validators_reject_wrong_capability_and_normalize_shared_text_contract() -> None:
    point = coordinate()
    candidate = GeocodeCandidate(formatted_address="地址", coordinate=point)
    place = PlaceSummary(uid="u", name="地点", address="地址", coordinate=point)
    details = PlaceDetails(uid="u", name="地点", address="地址", coordinate=point)
    step = RouteStep(instruction="直行", distance_m=1, duration_s=1, start=point, end=point)
    alternative = RouteAlternative(distance_m=1, duration_s=1, steps=(step,))
    matrix_cell = RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=1, duration_s=1)
    wrong = provenance(MapCapability.WEATHER)
    invalid_outcomes = (
        lambda: GeocodeSuccess(result=candidate, provenance=wrong),
        lambda: GeocodeNoResult(provenance=wrong),
        lambda: GeocodeAmbiguous(candidates=(candidate, candidate), provenance=wrong),
        lambda: ReverseGeocodeSuccess(formatted_address="地址", address_component=AddressComponent(), provenance=wrong),
        lambda: ReverseGeocodeNoResult(provenance=wrong),
        lambda: PlaceSearchSuccess(places=(place,), provenance=wrong),
        lambda: PlaceSearchNoResult(provenance=wrong),
        lambda: PlaceDetailsSuccess(details=details, provenance=wrong),
        lambda: PlaceDetailsNoResult(provenance=wrong),
        lambda: RouteMatrixSuccess(origin_count=1, destination_count=1, cells=(matrix_cell,), provenance=wrong),
        lambda: RouteMatrixNoResult(provenance=wrong),
        lambda: DirectionsSuccess(alternatives=(alternative,), provenance=wrong),
        lambda: DirectionsNoResult(provenance=wrong),
        lambda: MapLaunchSuccess(url="https://map.baidu.com/search", provenance=wrong),
        lambda: MapLaunchNoResult(provenance=wrong),
    )
    for make_outcome in invalid_outcomes:
        with pytest.raises(ValidationError):
            make_outcome()
    wrong_weather = provenance(MapCapability.GEOCODING)
    with pytest.raises(ValidationError):
        WeatherSuccess(current=CurrentWeather(condition="晴", temperature_c=20), provenance=wrong_weather)
    with pytest.raises(ValidationError):
        WeatherNoResult(provenance=wrong_weather)
    assert _clean_text_tuple((" 甲 ", "乙")) == ("甲", "乙")
    with pytest.raises(ValueError):
        _clean_text_tuple((" ",))
    with pytest.raises(ValidationError):
        GeocodeRequest(address=" ")
    with pytest.raises(ValidationError):
        MapProvenance(
            capability=MapCapability.GEOCODING,
            retrieved_at=datetime(2026, 8, 20),
            verified=True,
        )
    with pytest.raises(ValidationError):
        MapProvenance.model_validate_json(
            '{"capability":"geocoding","retrieved_at":"not-a-date","verified":true}'
        )
    assert _clean_optional_text(None) is None
    assert _clean_optional_text(" 文字 ") == "文字"
    with pytest.raises(ValueError):
        _clean_optional_text(" ")
    with pytest.raises(ValidationError):
        BaiduCoordinate.model_validate("not-an-object")
    for url in ("x" * 2049, "https://map.baidu.com:bad/path", "https:///path"):
        with pytest.raises(ValueError):
            _validate_safe_map_url(url)
    with pytest.raises(ValidationError):
        PlaceDetails(uid="u", name="n", address="a", coordinate=point, business_hours=(" ",))
    with pytest.raises(ValidationError):
        RouteMatrixSuccess(
            origin_count=100,
            destination_count=100,
            cells=(matrix_cell,),
            provenance=provenance(MapCapability.ROUTE_MATRIX),
        )
    with pytest.raises(ValidationError):
        DailyWeatherForecast.model_validate_json(
            '{"date":"not-a-date","day_condition":"晴","night_condition":"晴","high_c":20,"low_c":10}'
        )
