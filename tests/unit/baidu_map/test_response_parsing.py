from __future__ import annotations

from datetime import datetime, timezone

import pytest
from mcp.types import AudioContent, CallToolResult, EmbeddedResource, ImageContent, ResourceLink, TextContent

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapProtocolError,
    BaiduMapResponseError,
    CitySearchArea,
    DirectionsNoResult,
    DirectionsRequest,
    MatrixTravelMode,
    MapCapability,
    PlaceDetailsRequest,
    PlaceSearchNoResult,
    PlaceSearchRequest,
    ReverseGeocodeRequest,
    RouteEndpoint,
    RouteMatrixNoResult,
    RouteMatrixRequest,
    TravelMode,
    WeatherRequest,
)
from app.baidu_map._response_parsing import (
    parse_directions,
    parse_geocode,
    parse_place_details,
    parse_place_search,
    parse_reverse_geocode,
    parse_route_matrix,
    parse_weather,
    select_response_object,
)
import app.baidu_map._response_parsing as parsing
from tests.support.baidu_map_fake_mcp import structured_result, text_result


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
POINT = BaiduCoordinate(latitude=30.25, longitude=120.16)


def _step(instruction: str = "前行", *, string_coordinate: bool = False) -> dict[str, object]:
    lat: object = "30.25" if string_coordinate else 30.25
    lng: object = "120.16" if string_coordinate else 120.16
    return {
        "instruction": instruction,
        "distance": 10,
        "duration": 5,
        "start_location": {"lat": lat, "lng": lng},
        "end_location": {"lat": lat, "lng": lng},
    }


def _directions_request(mode: TravelMode = TravelMode.WALKING) -> DirectionsRequest:
    endpoint = RouteEndpoint(coordinate=POINT, label="不得外发")
    return DirectionsRequest(mode=mode, origin=endpoint, destination=endpoint, alternatives_limit=1)


def test_content_selection_structured_priority_and_full_json_fallback() -> None:
    selected = select_response_object(
        CallToolResult(
            content=[TextContent(type="text", text='{"status": 9}')],
            structuredContent={"status": 0},
        ),
        MapCapability.GEOCODING,
    )
    assert selected == {"status": 0}
    assert select_response_object(text_result({"status": 0}), MapCapability.GEOCODING) == {"status": 0}


@pytest.mark.parametrize(
    "block",
    [
        ImageContent(type="image", data="AA==", mimeType="image/png"),
        AudioContent.model_construct(type="audio", data="AA==", mimeType="audio/wav"),
        ResourceLink.model_construct(type="resource_link", uri="https://example.invalid"),
        EmbeddedResource.model_construct(type="resource", resource=object()),
        object(),
    ],
)
def test_structured_content_rejects_forbidden_or_unknown_blocks(block: object) -> None:
    result = CallToolResult.model_construct(
        content=[block], structuredContent={"status": 0}, isError=False
    )
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(result, MapCapability.GEOCODING)


def test_structured_content_error_flag_wins_over_content_selection() -> None:
    result = CallToolResult.model_construct(
        content=[TextContent(type="text", text='{"status": 0}')],
        structuredContent={"status": 0},
        isError=True,
    )
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(result, MapCapability.GEOCODING)


def test_structured_content_requires_a_text_content_list() -> None:
    result = CallToolResult.model_construct(content=None, structuredContent={"status": 0})
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(result, MapCapability.GEOCODING)


@pytest.mark.parametrize(
    "result",
    [
        CallToolResult(content=[], isError=True),
        CallToolResult(content=[TextContent(type="text", text="prefix {}")]),
        CallToolResult(content=[TextContent(type="text", text="[]")]),
        CallToolResult(content=[TextContent(type="text", text="{}"), TextContent(type="text", text="{}")]),
        CallToolResult(content=[ImageContent(type="image", data="AA==", mimeType="image/png")]),
    ],
)
def test_content_selection_rejects_protocol_shapes(result: CallToolResult) -> None:
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(result, MapCapability.GEOCODING)


def test_selected_json_enforces_one_mebibyte_and_json_tree_boundaries() -> None:
    with pytest.raises(BaiduMapResponseError):
        select_response_object(
            structured_result({"status": 0, "padding": "x" * (1024 * 1024)}),
            MapCapability.GEOCODING,
        )


def test_response_parser_primitive_guards_cover_type_and_boundary_contracts() -> None:
    capability = MapCapability.GEOCODING
    with pytest.raises(BaiduMapResponseError):
        parsing._object(None, capability)
    with pytest.raises(BaiduMapResponseError):
        parsing._array({}, capability)
    with pytest.raises(BaiduMapResponseError):
        parsing._strict_int(True, capability)
    assert parsing._number(2, capability, minimum=0, maximum=2) == 2.0
    assert parsing._number("2.50", capability, allow_decimal_string=True) == 2.5
    for value, kwargs in (("bad", {}), (float("inf"), {}), (-1, {"minimum": 0}), (3, {"maximum": 2})):
        with pytest.raises(BaiduMapResponseError):
            parsing._number(value, capability, **kwargs)
    with pytest.raises(BaiduMapResponseError):
        parsing._number(10**400, capability)
    with pytest.raises(BaiduMapResponseError):
        parsing._number("9" * 10000, capability, allow_decimal_string=True)
    for value in (None, 1, " ", "x" * 513):
        with pytest.raises(BaiduMapResponseError):
            parsing._required_text(value, capability)
    assert parsing._optional_text(None, capability) is None
    assert parsing._optional_text(" ", capability) is None
    for value in (1, "x" * 513):
        with pytest.raises(BaiduMapResponseError):
            parsing._optional_text(value, capability)
    assert parsing._adcode(None, capability) is None
    with pytest.raises(BaiduMapResponseError):
        parsing._adcode("01", capability)
    with pytest.raises(BaiduMapResponseError):
        parsing._coordinate({"lat": 91, "lng": 0}, capability)
    with pytest.raises(BaiduMapResponseError):
        parsing._construct(BaiduCoordinate, capability, latitude=0)
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(object(), capability)  # type: ignore[arg-type]
    with pytest.raises(BaiduMapProtocolError):
        select_response_object(
            CallToolResult.model_construct(content=[], structuredContent=[]), capability
        )
    with pytest.raises(BaiduMapResponseError):
        select_response_object(
            CallToolResult.model_construct(content=[], structuredContent={"bad": object()}), capability
        )


def test_response_parsers_reject_status_shapes_and_leaf_contract_drift() -> None:
    geocode_request = __import__("app.baidu_map", fromlist=["GeocodeRequest"]).GeocodeRequest(address="地址")
    with pytest.raises(BaiduMapResponseError):
        parse_geocode({"status": "0", "result": {}}, geocode_request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_geocode({"status": 0, "result": {}}, geocode_request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_geocode({"status": 0, "result": {"location": {"lat": 0, "lng": 0}, "confidence": 101}}, geocode_request, NOW)
    reverse_request = ReverseGeocodeRequest(location=POINT)
    with pytest.raises(BaiduMapResponseError):
        parse_reverse_geocode({"status": 0, "result": {}}, reverse_request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_reverse_geocode(
            {"status": 0, "result": {"formatted_address": "地址", "addressComponent": {"adcode": 0}, "pois": [{"uid": "u", "name": "n", "addr": "a", "point": "bad"}]}},
            reverse_request,
            NOW,
        )
    incomplete = parse_reverse_geocode(
        {"status": 0, "result": {"formatted_address": "地址", "addressComponent": {}, "pois": [{"uid": "u", "name": "n", "addr": "a", "point": {}}]}},
        reverse_request,
        NOW,
    )
    assert incomplete.nearby_places == ()
    details_request = PlaceDetailsRequest(uid="u")
    with pytest.raises(BaiduMapResponseError):
        parse_place_details({"status": 0, "result": {}}, details_request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_place_details(
            {"status": 0, "result": {"uid": "other", "name": "n", "address": "a", "location": {"lat": 0, "lng": 0}}},
            details_request,
            NOW,
        )
    matrix_request = RouteMatrixRequest(mode=MatrixTravelMode.WALKING, origins=(POINT,), destinations=(POINT,))
    with pytest.raises(BaiduMapResponseError):
        parse_route_matrix({"status": 0, "result": [{"distance": {"value": 1}, "duration": {"value": 1}}, {}]}, matrix_request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_route_matrix(
            {"status": 0, "result": [{"distance": {}, "duration": "bad"}]}, matrix_request, NOW
        )
    directions_request = _directions_request(TravelMode.WALKING)
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [{**_step(" ")}]}]}},
            directions_request,
            NOW,
        )
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [_step()]}] * 4}},
            directions_request,
            NOW,
        )
    weather_request = WeatherRequest(location=POINT)
    with pytest.raises(BaiduMapResponseError):
        parse_weather({"status": 0, "result": {}}, weather_request, NOW)
    invalid_date = {"date": "2026-02-30", "text_day": "晴", "text_night": "晴", "high": 20, "low": 10}
    with pytest.raises(BaiduMapResponseError):
        parse_weather(
            {"status": 0, "result": {"now": {"text": "晴", "temp": 20}, "forecasts": [invalid_date]}},
            weather_request,
            NOW,
        )
    with pytest.raises(BaiduMapResponseError):
        parse_weather(
            {
                "status": 0,
                "result": {
                    "now": {"text": "晴", "temp": 20},
                    "forecasts": [
                        {
                            "date": 20260821,
                            "text_day": "晴",
                            "text_night": "晴",
                            "high": 20,
                            "low": 10,
                        }
                    ],
                },
            },
            weather_request,
            NOW,
        )
    with pytest.raises(BaiduMapResponseError):
        select_response_object(
            CallToolResult(content=[], structuredContent={"status": 0, "bad": float("nan")}),
            MapCapability.GEOCODING,
        )


def test_geocode_success_and_null_failure() -> None:
    request = __import__("app.baidu_map", fromlist=["GeocodeRequest"]).GeocodeRequest(address="西湖")
    outcome = parse_geocode(
        {"status": 0, "result": {"location": {"lat": 30.25, "lng": 120.16}, "confidence": 88}},
        request,
        NOW,
    )
    assert outcome.result.formatted_address == "西湖"
    assert outcome.result.confidence == 0.88
    with pytest.raises(BaiduMapResponseError):
        parse_geocode({"status": 0, "result": None}, request, NOW)


@pytest.mark.parametrize("adcode", [330106, "330106"])
def test_reverse_geocode_adcode_and_incomplete_poi_filter(adcode: object) -> None:
    outcome = parse_reverse_geocode(
        {
            "status": 0,
            "result": {
                "formatted_address": "地址",
                "addressComponent": {"country": "中国", "adcode": adcode},
                "pois": [
                    {"uid": "drop", "name": "缺地址", "point": {"x": 120.1, "y": 30.2}},
                    {"uid": "ok", "name": "地点", "addr": "地址", "point": {"x": 120.1, "y": 30.2}, "tag": "景点"},
                ],
            },
        },
        ReverseGeocodeRequest(location=POINT),
        NOW,
    )
    assert outcome.address_component.adcode == 330106
    assert tuple(place.uid for place in outcome.nearby_places) == ("ok",)
    assert outcome.nearby_places[0].address == "地址"
    assert outcome.nearby_places[0].coordinate.latitude == 30.2
    assert outcome.nearby_places[0].coordinate.longitude == 120.1
    assert outcome.nearby_places[0].category == "景点"


@pytest.mark.parametrize(
    ("raw_count", "public_count"), ((0, 0), (20, 20), (21, 20), (37, 20))
)
def test_reverse_geocode_projects_first_twenty_after_validating_provider_pois(
    raw_count: int, public_count: int
) -> None:
    pois = [
        {
            "uid": f"u{index}",
            "name": f"地点{index}",
            "addr": f"地址{index}",
            "point": {"x": 120.1, "y": 30.2},
            "tag": "景点",
        }
        for index in range(raw_count)
    ]
    payload = {
        "status": 0,
        "result": {
            "formatted_address": "地址",
            "addressComponent": {},
            "pois": pois,
        },
    }
    outcome = parse_reverse_geocode(
        payload, ReverseGeocodeRequest(location=POINT), NOW
    )
    assert tuple(place.uid for place in outcome.nearby_places) == tuple(
        f"u{index}" for index in range(public_count)
    )


def test_reverse_geocode_validates_projected_away_provider_pois() -> None:
    pois = [
        {
            "uid": f"u{index}",
            "name": f"地点{index}",
            "addr": f"地址{index}",
            "point": {"x": 120.1, "y": 30.2},
        }
        for index in range(21)
    ]
    payload = {
        "status": 0,
        "result": {
            "formatted_address": "地址",
            "addressComponent": {},
            "pois": pois,
        },
    }
    pois[20]["point"] = {"x": "120.1", "y": 30.2}
    with pytest.raises(BaiduMapResponseError):
        parse_reverse_geocode(
            payload, ReverseGeocodeRequest(location=POINT), NOW
        )


def test_reverse_geocode_does_not_accept_search_place_aliases() -> None:
    outcome = parse_reverse_geocode(
        {
            "status": 0,
            "result": {
                "formatted_address": "地址",
                "addressComponent": {},
                "pois": [
                    {
                        "uid": "legacy",
                        "name": "旧字段",
                        "address": "地址",
                        "location": {"lat": 30.2, "lng": 120.1},
                    }
                ],
            },
        },
        ReverseGeocodeRequest(location=POINT),
        NOW,
    )
    assert outcome.nearby_places == ()


@pytest.mark.parametrize(
    "poi",
    (
        {"name": "缺 UID", "addr": "地址", "point": "bad"},
        {"uid": "u", "name": "缺 point", "addr": "地址", "tag": []},
        {"uid": "u", "name": "缺 y", "addr": "地址", "point": {"x": "120.1"}},
    ),
)
def test_reverse_geocode_validates_present_fields_before_dropping_incomplete_poi(
    poi: object,
) -> None:
    with pytest.raises(BaiduMapResponseError):
        parse_reverse_geocode(
            {
                "status": 0,
                "result": {
                    "formatted_address": "地址",
                    "addressComponent": {},
                    "pois": [poi],
                },
            },
            ReverseGeocodeRequest(location=POINT),
            NOW,
        )


@pytest.mark.parametrize("adcode", [True, " 1", "+1", "01", "1.0", "1e2", 1_000_000])
def test_reverse_geocode_rejects_malformed_adcode(adcode: object) -> None:
    with pytest.raises(BaiduMapResponseError):
        parse_reverse_geocode(
            {"status": 0, "result": {"formatted_address": "地址", "addressComponent": {"adcode": adcode}}},
            ReverseGeocodeRequest(location=POINT),
            NOW,
        )


def test_place_search_no_result_filter_limit_and_absolute_capacity() -> None:
    request = PlaceSearchRequest(
        query="博物馆", area=CitySearchArea(region="杭州"), result_limit=1
    )
    assert isinstance(parse_place_search({"status": 0, "results": []}, request, NOW), PlaceSearchNoResult)
    assert isinstance(
        parse_place_search(
            {"status": 0, "results": [{"uid": "incomplete"}]}, request, NOW
        ),
        PlaceSearchNoResult,
    )
    assert isinstance(
        parse_place_search(
            {
                "status": 0,
                "results": [
                    {
                        "uid": "u",
                        "name": "n",
                        "address": "a",
                        "location": {"lat": 30.2},
                    }
                ],
            },
            request,
            NOW,
        ),
        PlaceSearchNoResult,
    )
    with pytest.raises(BaiduMapResponseError):
        parse_place_search(
            {
                "status": 0,
                "results": [
                    {
                        "uid": "u",
                        "name": "n",
                        "address": "a",
                        "location": "bad",
                    }
                ],
            },
            request,
            NOW,
        )
    raw_place = {"uid": "u", "name": "n", "address": "a", "location": {"lat": 30.2, "lng": 120.1}, "tag": "only-tag"}
    success = parse_place_search({"status": 0, "results": [raw_place, {**raw_place, "uid": "u2"}]}, request, NOW)
    assert tuple(place.uid for place in success.places) == ("u",)
    assert success.places[0].category == "only-tag"
    real_provider_shape = {
        **raw_place,
        "tag": None,
        "detail_info": {
            "tag": "酒店",
            "classified_poi_tag": "酒店;经济型",
        },
    }
    success = parse_place_search(
        {"status": 0, "results": [real_provider_shape]}, request, NOW
    )
    assert success.places[0].category == "酒店"
    classified_only = {
        **raw_place,
        "tag": None,
        "detail_info": {"classified_poi_tag": "旅游景点;博物馆"},
    }
    success = parse_place_search(
        {"status": 0, "results": [classified_only]}, request, NOW
    )
    assert success.places[0].category == "旅游景点;博物馆"
    with pytest.raises(BaiduMapResponseError):
        parse_place_search({"status": 0, "results": [raw_place] * 51}, request, NOW)


@pytest.mark.parametrize(
    "item",
    [
        {"name": 42, "address": "a", "location": {"lat": 30.2, "lng": 120.1}},
        {"name": "n", "address": "a", "location": {"lat": 30.2, "lng": 120.1}, "tag": []},
        {"uid": "u", "name": "n", "address": "a", "tag": []},
        {"uid": "u", "name": "n", "address": "a", "location": {"lat": "30.2"}},
        {"uid": "u", "name": "n", "address": "a", "location": {"lat": 91}},
        {"uid": "u", "name": "n", "address": "a", "location": {"lng": True}},
        {"uid": "u", "name": "n", "address": "a", "location": {"lng": float("nan")}},
        {"uid": "u", "name": "n", "address": "a", "location": {"lng": float("inf")}},
        {"uid": "u", "name": "n", "address": "a", "location": {"lng": 181}},
    ],
)
def test_place_search_validates_present_fields_before_dropping_incomplete_item(
    item: dict[str, object],
) -> None:
    with pytest.raises(BaiduMapResponseError):
        parse_place_search({"status": 0, "results": [item]}, request=PlaceSearchRequest(
            query="博物馆", area=CitySearchArea(region="杭州")
        ), retrieved_at=NOW)


def test_place_search_mixed_incomplete_malformed_item_fails_whole_response() -> None:
    valid = {"uid": "u", "name": "n", "address": "a", "location": {"lat": 30.2, "lng": 120.1}}
    incomplete_malformed = {"name": "n2", "address": "a2", "location": {"lat": "30.2"}}
    with pytest.raises(BaiduMapResponseError):
        parse_place_search(
            {"status": 0, "results": [valid, incomplete_malformed]},
            PlaceSearchRequest(query="博物馆", area=CitySearchArea(region="杭州")),
            NOW,
        )


@pytest.mark.parametrize(
    "item",
    [
        {"uid": "u", "address": "a", "location": {"lat": 30.2, "lng": 120.1}},
        {"uid": "u", "name": "n", "location": {"lat": 30.2, "lng": 120.1}},
    ],
)
def test_place_search_drops_items_missing_required_identity_fields(
    item: dict[str, object],
) -> None:
    outcome = parse_place_search(
        {"status": 0, "results": [item]},
        PlaceSearchRequest(query="博物馆", area=CitySearchArea(region="杭州")),
        NOW,
    )
    assert isinstance(outcome, PlaceSearchNoResult)


def test_place_details_uses_only_detail_tag_and_exact_uid() -> None:
    request = PlaceDetailsRequest(uid="u")
    success = parse_place_details(
        {
            "status": 0,
            "result": {
                "uid": "u", "name": "地点", "address": "地址",
                "location": {"lat": 30.2, "lng": 120.1},
                "tag": "wrong", "telephone": "123",
                "detail_info": {"tag": "detail-tag", "overall_rating": "4.5", "shop_hours": "09:00-18:00"},
            },
        },
        request,
        NOW,
    )
    assert success.details.category == "detail-tag"
    assert success.details.business_hours == ("09:00-18:00",)
    with pytest.raises(BaiduMapResponseError):
        parse_place_details({"status": 0, "result": None}, request, NOW)


def test_matrix_partial_and_all_no_result() -> None:
    request = RouteMatrixRequest(mode=MatrixTravelMode.WALKING, origins=(POINT,), destinations=(POINT, POINT))
    partial = parse_route_matrix(
        {
            "status": 0,
            "result": [
                {"distance": {"value": 10}, "duration": {"value": 5}},
                {"distance": {}, "duration": {}},
            ],
        },
        request,
        NOW,
    )
    assert partial.cells[1].status.value == "no_result"
    assert isinstance(parse_route_matrix({"status": 0, "result": [{}, {}]}, request, NOW), RouteMatrixNoResult)
    assert isinstance(parse_route_matrix({"status": 0, "result": []}, request, NOW), RouteMatrixNoResult)
    with pytest.raises(BaiduMapResponseError):
        parse_route_matrix({"status": 0, "result": [{"distance": {"value": 1}}, {}]}, request, NOW)
    with pytest.raises(BaiduMapResponseError):
        parse_route_matrix(
            {
                "status": 0,
                "result": [{"distance": {"value": 1}, "duration": {}} , {}],
            },
            request,
            NOW,
        )
    with pytest.raises(BaiduMapResponseError):
        parse_route_matrix(
            {"status": 0, "result": [{"distance": None, "duration": {}}, {}]},
            request,
            NOW,
        )


@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.DRIVING, TravelMode.RIDING])
def test_nontransit_directions_require_flat_nonblank_steps(mode: TravelMode) -> None:
    outcome = parse_directions(
        {"status": 0, "result": {"routes": [{"distance": 10, "duration": 5, "steps": [_step(string_coordinate=True)]}]}},
        _directions_request(mode),
        NOW,
    )
    assert outcome.alternatives[0].steps[0].instruction == "前行"
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 10, "duration": 5, "steps": [[_step()]]}]}},
            _directions_request(mode), NOW,
        )


def test_transit_validates_then_filters_blank_steps_in_order() -> None:
    raw = {"status": 0, "result": {"routes": [{"distance": 10, "duration": 5, "steps": [[_step(" "), _step("一")], [_step("\t"), _step("二"), _step("\n")]]}]}}
    outcome = parse_directions(raw, _directions_request(TravelMode.TRANSIT), NOW)
    assert tuple(step.instruction for step in outcome.alternatives[0].steps) == ("一", "二")
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [[_step(" ")]]}]}},
            _directions_request(TravelMode.TRANSIT), NOW,
        )


def test_transit_validates_raw_capacity_and_filtered_step_fields_before_filtering() -> None:
    too_many = [_step(" ") for _ in range(128)] + [_step("保留")]
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [too_many]}]}},
            _directions_request(TravelMode.TRANSIT), NOW,
        )
    malformed_blank = _step(" ")
    malformed_blank["duration"] = "1"
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [[malformed_blank, _step("保留")]]}]}},
            _directions_request(TravelMode.TRANSIT), NOW,
        )


@pytest.mark.parametrize("shape", ["plural", "mixed", "third", "missing"])
def test_transit_rejects_alias_mixed_deeper_and_missing_fields(shape: str) -> None:
    step = _step("乘车")
    if shape == "plural":
        step["instructions"] = step.pop("instruction")
        steps: object = [[step]]
    elif shape == "mixed":
        steps = [[step], _step("步行")]
    elif shape == "third":
        steps = [[[step]]]
    else:
        step.pop("end_location")
        steps = [[step]]
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": steps}]}},
            _directions_request(TravelMode.TRANSIT), NOW,
        )


@pytest.mark.parametrize("coordinate", [" 30", "+30", "03", "3e1", "NaN", True, 91])
def test_directions_step_rejects_non_ascii_decimal_forms(coordinate: object) -> None:
    step = _step()
    step["start_location"] = {"lat": coordinate, "lng": 120.1}
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [{"distance": 1, "duration": 1, "steps": [step]}]}},
            _directions_request(), NOW,
        )


def test_non_step_coordinate_paths_never_accept_decimal_strings() -> None:
    request = __import__("app.baidu_map", fromlist=["GeocodeRequest"]).GeocodeRequest(address="x")
    with pytest.raises(BaiduMapResponseError):
        parse_geocode(
            {"status": 0, "result": {"location": {"lat": "30.2", "lng": 120.1}}},
            request, NOW,
        )


def test_directions_empty_routes_is_only_directions_no_result_shape() -> None:
    outcome = parse_directions(
        {"status": 0, "result": {"routes": []}}, _directions_request(), NOW
    )
    assert isinstance(outcome, DirectionsNoResult)
    with pytest.raises(BaiduMapResponseError):
        parse_directions(
            {"status": 0, "result": {"routes": [], "unexpected": True}},
            _directions_request(), NOW,
        )


def test_weather_validates_all_seven_then_projects_first_five() -> None:
    forecasts = [
        {"date": f"2026-08-{21 + index:02d}", "text_day": "晴", "text_night": "晴", "high": "30", "low": "20"}
        for index in range(7)
    ]
    payload = {"status": 0, "result": {"now": {"text": "晴", "temp": "28", "feels_like": "29", "rh": "50", "wind_class": "2级"}, "forecasts": forecasts}}
    outcome = parse_weather(payload, WeatherRequest(location=POINT), NOW)
    assert len(outcome.forecast) == 5
    forecasts[6]["high"] = "bad"
    with pytest.raises(BaiduMapResponseError):
        parse_weather(payload, WeatherRequest(location=POINT), NOW)


@pytest.mark.parametrize("count", [0, 1, 5, 6, 7])
def test_weather_accepted_raw_forecast_cardinalities(count: int) -> None:
    forecasts = [
        {"date": f"2026-09-{index + 1:02d}", "text_day": "晴", "text_night": "晴", "high": 30, "low": 20}
        for index in range(count)
    ]
    outcome = parse_weather(
        {"status": 0, "result": {"now": {"text": "晴", "temp": 28}, "forecasts": forecasts}},
        WeatherRequest(location=POINT), NOW,
    )
    assert len(outcome.forecast) == min(count, 5)


def test_weather_rejects_eight_forecasts_and_malformed_sixth_item() -> None:
    forecasts = [
        {"date": f"2026-09-{index + 1:02d}", "text_day": "晴", "text_night": "晴", "high": 30, "low": 20}
        for index in range(8)
    ]
    payload = {"status": 0, "result": {"now": {"text": "晴", "temp": 28}, "forecasts": forecasts}}
    with pytest.raises(BaiduMapResponseError):
        parse_weather(payload, WeatherRequest(location=POINT), NOW)
    payload["result"]["forecasts"] = forecasts[:6]
    forecasts[5]["low"] = 40
    with pytest.raises(BaiduMapResponseError):
        parse_weather(payload, WeatherRequest(location=POINT), NOW)


@pytest.mark.parametrize(
    "parser,request_value,payload",
    [
        (parse_reverse_geocode, ReverseGeocodeRequest(location=POINT), {"status": 0, "result": None}),
        (parse_place_details, PlaceDetailsRequest(uid="u"), {"status": 0, "result": None}),
        (parse_weather, WeatherRequest(location=POINT), {"status": 0, "result": None}),
    ],
)
def test_non_allowlisted_null_result_fails(parser, request_value, payload) -> None:
    with pytest.raises(BaiduMapResponseError):
        parser(payload, request_value, NOW)


def test_status_nonzero_never_uses_free_text_classification() -> None:
    request = PlaceSearchRequest(query="x", area=CitySearchArea(region="y"))
    with pytest.raises(BaiduMapResponseError):
        parse_place_search({"status": 401, "message": "auth or arbitrary secret"}, request, NOW)
