"""确定性查询计划与 hash 合同。"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.baidu_map import BaiduCoordinate, CitySearchArea, PlaceSearchRequest, PlaceSummary
from app.hotel_selection.errors import HotelInputError
from app.hotel_selection.query_policy import (
    build_queries,
    build_query_plan_hash,
    hash_query_plan,
    hash_request,
    project_candidates,
)
import app.hotel_selection.query_policy as query_policy_module
from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    TravelPreferences,
    TravelRequirement,
)


def confirmed(
    preference: str | None = "西湖附近", *, destination: str = " 杭州 "
) -> ConfirmedTravelRequirement:
    return ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=TravelRequirement(
            destination=destination,
            preferences=TravelPreferences(hotel_preferences=preference),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="confirmed-digest",
    )


def test_build_queries_is_exactly_ordered_typed_and_bounded() -> None:
    requests = build_queries(confirmed())
    assert tuple(request.query for request in requests) == ("西湖附近 酒店", "酒店", "宾馆")
    assert all(request.result_limit == 10 for request in requests)
    assert all(request.tag is None for request in requests)
    assert all(isinstance(request.area, CitySearchArea) for request in requests)
    assert all(request.area.region == "杭州" for request in requests)
    assert len(build_queries(confirmed("酒店"))) == 3
    repeated_source = build_queries(confirmed("酒店"))
    assert tuple(request.query for request in repeated_source) == (
        "酒店 酒店",
        "酒店",
        "宾馆",
    )
    assert len({request.query for request in repeated_source}) == len(repeated_source)
    assert tuple(request.query for request in build_queries(confirmed(None))) == ("酒店", "宾馆", "住宿")


def test_query_hashes_are_deterministic_order_sensitive_and_content_hashes() -> None:
    requests = build_queries(confirmed())
    assert hash_request(requests[0]) == hash_request(requests[0])
    assert hash_query_plan(requests) == build_query_plan_hash(requests)
    assert hash_query_plan(requests) != hash_query_plan(tuple(reversed(requests)))
    changed = PlaceSearchRequest(
        schema_version="1.0",
        query="另一个查询",
        tag=None,
        area=CitySearchArea(schema_version="1.0", area_type="city", region="杭州"),
        result_limit=10,
    )
    assert hash_request(changed) != hash_request(requests[0])
    assert requests[0].query not in hash_request(requests[0])
    assert requests[0].query not in hash_query_plan(requests)
    assert hash_request(requests[0]).startswith("sha256:")


def test_query_normalization_is_only_outer_strip_and_hash_binds_area_and_order() -> None:
    requests = build_queries(confirmed("  西  湖  ", destination="  杭  州  "))
    assert requests[0].query == "西  湖 酒店"
    assert requests[0].area.region == "杭  州"
    assert requests[0].result_limit == 10
    assert requests[0].tag is None
    assert type(requests[0].area) is CitySearchArea

    composed = build_queries(confirmed("é", destination="é"))[0]
    decomposed = build_queries(confirmed("e\u0301", destination="e\u0301"))[0]
    assert composed.query != decomposed.query
    assert composed.area.region != decomposed.area.region
    assert hash_request(composed) != hash_request(decomposed)

    punctuation = build_queries(confirmed("  亲子，酒店！  "))[0]
    assert punctuation.query == "亲子，酒店！ 酒店"
    assert hash_query_plan(build_queries(confirmed(destination="杭州"))) != hash_query_plan(
        build_queries(confirmed(destination="宁波"))
    )
    digest = hash_query_plan(build_queries(confirmed()))
    assert "杭州" not in digest
    assert "西湖附近" not in digest


def test_blank_inputs_and_wrong_shapes_fail_closed() -> None:
    with __import__("pytest").raises(HotelInputError):
        build_queries(confirmed("   "))
    with __import__("pytest").raises(HotelInputError):
        forged_preferences = TravelPreferences.model_construct(hotel_preferences="酒店" * 600)
        forged_requirement = TravelRequirement.model_construct(
            destination="杭州", preferences=forged_preferences
        )
        forged = ConfirmedTravelRequirement.model_construct(
            schema_version="1.0",
            requirement=forged_requirement,
            metadata_without_raw_evidence=FieldMetadataRegistry(),
            confirmation_summary_digest="confirmed-digest",
        )
        build_queries(forged)
    with __import__("pytest").raises(HotelInputError):
        hash_query_plan(list(build_queries(confirmed())))


def test_query_length_boundary_is_bounded_without_truncation() -> None:
    boundary = PlaceSearchRequest(
        schema_version="1.0",
        query="x" * 512,
        tag=None,
        area=CitySearchArea(schema_version="1.0", area_type="city", region="杭州"),
        result_limit=10,
    )
    assert len(boundary.query) == 512
    with __import__("pytest").raises(ValidationError):
        PlaceSearchRequest(
            schema_version="1.0",
            query="x" * 513,
            tag=None,
            area=CitySearchArea(schema_version="1.0", area_type="city", region="杭州"),
            result_limit=10,
        )
    at_upstream_limit = build_queries(confirmed("x" * 500))[0]
    assert len(at_upstream_limit.query) == 503
    assert len(at_upstream_limit.query) <= 512
    with __import__("pytest").raises(HotelInputError):
        forged_preferences = TravelPreferences.model_construct(hotel_preferences="x" * 501)
        forged_requirement = TravelRequirement.model_construct(
            destination="杭州", preferences=forged_preferences
        )
        forged = ConfirmedTravelRequirement.model_construct(
            schema_version="1.0",
            requirement=forged_requirement,
            metadata_without_raw_evidence=FieldMetadataRegistry(),
            confirmation_summary_digest="confirmed-digest",
        )
        build_queries(forged)


def test_place_summary_public_shape_is_available_for_qualification_tests() -> None:
    place = PlaceSummary(
        uid="uid-1",
        name="酒店",
        address="地址",
        coordinate=BaiduCoordinate(latitude=30.2, longitude=120.1),
        category="酒店",
    )
    assert place.coordinate.coordinate_system.value == "bd09ll"


def test_query_policy_rejects_wrong_types_and_internal_serialization_failures(monkeypatch) -> None:
    request_type = query_policy_module.PlaceSearchRequest
    original_model_dump = request_type.model_dump
    with pytest.raises(HotelInputError):
        query_policy_module.build_queries(object())
    with pytest.raises(HotelInputError):
        hash_request(object())
    with pytest.raises(HotelInputError):
        hash_query_plan(list(build_queries(confirmed())))
    with pytest.raises(HotelInputError):
        hash_query_plan((build_queries(confirmed())[0], object(), build_queries(confirmed())[2]))
    with pytest.raises(HotelInputError):
        query_policy_module.project_candidates((object(),))
    with pytest.raises(HotelInputError):
        project_candidates([])
    with pytest.raises(HotelInputError):
        project_candidates(tuple(PlaceSummary(
            uid=f"too-many-{index}",
            name="酒店",
            address="地址",
            coordinate=BaiduCoordinate(latitude=30.2, longitude=120.1),
            category="酒店",
        ) for index in range(11)))

    repeated = PlaceSummary(
        uid="repeated",
        name="酒店",
        address="地址",
        coordinate=BaiduCoordinate(latitude=30.2, longitude=120.1),
        category="酒店",
    )
    assert len(project_candidates((repeated, repeated))) == 1

    valid_request = build_queries(confirmed())[0]

    class DuplicateRequest:
        def __init__(self, **_: object) -> None:
            self.query = "same"

    monkeypatch.setattr(query_policy_module, "PlaceSearchRequest", DuplicateRequest)
    with pytest.raises(HotelInputError):
        query_policy_module.build_queries(confirmed())

    class ExplodingRequest:
        def __init__(self, **_: object) -> None:
            raise ValueError("request construction failure")

    monkeypatch.setattr(query_policy_module, "PlaceSearchRequest", ExplodingRequest)
    with pytest.raises(HotelInputError):
        query_policy_module.build_queries(confirmed())

    monkeypatch.setattr(query_policy_module, "PlaceSearchRequest", request_type)
    monkeypatch.setattr(request_type, "model_dump", lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("dump")))
    with pytest.raises(HotelInputError):
        hash_request(valid_request)
    with pytest.raises(HotelInputError):
        hash_query_plan((valid_request, valid_request, valid_request))

    monkeypatch.setattr(query_policy_module._PLACE_ADAPTER, "validate_python", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("place")))
    with pytest.raises(HotelInputError):
        query_policy_module.project_candidates(
            (
                PlaceSummary(
                    uid="uid-1",
                    name="酒店",
                    address="地址",
                    coordinate=BaiduCoordinate(latitude=30.2, longitude=120.1),
                    category="酒店",
                ),
            )
        )

    monkeypatch.setattr(request_type, "model_dump", original_model_dump)
    monkeypatch.setattr(query_policy_module.json, "dumps", lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("json")))
    with pytest.raises(HotelInputError):
        hash_request(valid_request)
