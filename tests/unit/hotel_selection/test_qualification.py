"""住宿类别资格、UID 身份冲突和候选容量合同。"""

from __future__ import annotations

import pytest

from app.baidu_map import BaiduCoordinate, PlaceSummary
from app.hotel_selection import HotelCandidate
from app.hotel_selection.errors import HotelCandidateIdentityConflictError
from app.hotel_selection.query_policy import project_candidates, qualify_category


def place(
    uid: str,
    *,
    name: str | None = None,
    address: str = "虚构地址",
    latitude: float = 30.2,
    longitude: float = 120.1,
    category: str | None = "酒店",
) -> PlaceSummary:
    return PlaceSummary(
        uid=uid,
        name=name or f"地点-{uid}",
        address=address,
        coordinate=BaiduCoordinate(latitude=latitude, longitude=longitude),
        category=category,
    )


@pytest.mark.parametrize(
    "category",
    (
        "住宿服务",
        "酒店",
        "星级酒店",
        "快捷酒店",
        "经济型酒店",
        "宾馆酒店",
        "宾馆",
        "旅馆",
        "客栈",
        "民宿",
        "度假村",
        "公寓式酒店",
        "青年旅舍",
        "餐饮, 酒店",
        "餐饮；酒店",
        "餐饮/酒店",
        "餐饮|酒店",
        "餐饮>酒店",
    ),
)
def test_exact_hotel_category_tokens_and_approved_separators(category: str) -> None:
    assert qualify_category(category)


@pytest.mark.parametrize("separator", (";", "；", ",", "，", "/", "|", ">"))
def test_each_approved_separator_handles_whitespace_empty_and_continuous_tokens(
    separator: str,
) -> None:
    assert qualify_category(f"餐饮 {separator} 酒店")
    assert qualify_category(f"餐饮{separator}{separator}酒店")
    assert qualify_category(f"  {separator}  {separator}  酒店  ")


@pytest.mark.parametrize("category", (None, "", "餐饮", "购物,景点", "酒店式公寓", "酒店／餐饮", "酒店\\餐饮", "酒店+餐饮"))
def test_category_negative_boundaries_and_no_name_fallback(category: str | None) -> None:
    assert not qualify_category(category)
    assert not qualify_category("名称叫酒店但类别为空" if category is None else category)


def test_name_never_qualifies_a_missing_or_non_hotel_category() -> None:
    values = project_candidates(
        (
            place("missing", name="酒店名称", category=None),
            place("non-hotel", name="酒店名称", category="餐饮"),
        )
    )
    assert values == ()


def test_first_qualified_occurrence_preserves_supplier_category_text() -> None:
    values = project_candidates(
        (
            place("same", category="餐饮"),
            place("same", category="餐饮； 酒店"),
        )
    )
    assert len(values) == 1
    assert values[0].category == "餐饮； 酒店"


@pytest.mark.parametrize("position", range(6, 11))
def test_identity_conflicts_at_positions_six_through_ten_fail_closed(position: int) -> None:
    values = [place(f"u{index}", category="酒店") for index in range(1, position)]
    values.append(place("u1", name=f"冲突-{position}", category="酒店"))
    with pytest.raises(HotelCandidateIdentityConflictError):
        project_candidates(tuple(values))


def test_uid_dedup_keeps_first_qualified_occurrence_and_exact_identity() -> None:
    values = project_candidates(
        (
            place("u1", category="景点"),
            place("u1", category="酒店"),
            place("u2", name="相似名称", category="酒店"),
            place("u3", category="酒店"),
            place("u4", category="酒店"),
            place("u5", category="酒店"),
            place("u6", category="酒店"),
        )
    )
    assert tuple(item.uid for item in values) == ("u1", "u2", "u3", "u4", "u5")
    assert all(isinstance(item, HotelCandidate) for item in values)


@pytest.mark.parametrize("field", ("name", "address", "latitude", "longitude"))
def test_same_uid_identity_conflict_is_fail_closed_even_when_category_is_invalid(field: str) -> None:
    first = place("same", category="景点")
    overrides = {
        "name": "另一个地点",
        "address": "另一个地址",
        "latitude": 31.2,
        "longitude": 121.1,
    }
    second = place("same", category="景点", **{field: overrides[field]})
    with pytest.raises(HotelCandidateIdentityConflictError):
        project_candidates((first, second))


def test_different_uid_similar_identity_is_retained_and_extra_items_are_checked() -> None:
    values = [place(f"u{i}", name="同名", category="酒店") for i in range(1, 7)]
    values.append(place("u1", name="冲突", category="景点"))
    with pytest.raises(HotelCandidateIdentityConflictError):
        project_candidates(tuple(values))
    retained = project_candidates(tuple(values[:6]))
    assert len(retained) == 5
    assert tuple(item.uid for item in retained) == tuple(f"u{i}" for i in range(1, 6))
