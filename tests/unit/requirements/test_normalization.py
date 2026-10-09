"""DLG-T03: deterministic normalization and validation tests."""

from datetime import date

import pytest

from app.requirements.models import TravelRequirement, TravelTimeWindow
from app.requirements.normalization import (
    NormalizationError,
    infer_hub_type,
    normalize_budget,
    normalize_relative_date,
    validate_budget,
    validate_party,
    validate_requirement_bounds,
    validate_rooms,
    validate_time_window,
)


@pytest.mark.parametrize(
    ("raw", "session_date", "expected"),
    [
        ("今天", date(2025, 6, 1), "2025-06-01"),
        ("明天", date(2025, 1, 31), "2025-02-01"),
        ("明天", date(2025, 12, 31), "2026-01-01"),
        ("后天", date(2025, 12, 30), "2026-01-01"),
    ],
)
def test_relative_dates_cross_calendar_boundaries(raw, session_date, expected):
    result, pending_confirmation = normalize_relative_date(raw, session_date)
    assert result == expected
    assert pending_confirmation is True


def test_absolute_date_is_strictly_validated():
    assert normalize_relative_date("2025-06-03", date(2025, 1, 1)) == ("2025-06-03", False)
    with pytest.raises(NormalizationError):
        normalize_relative_date("2025-02-30", date(2025, 1, 1))
    with pytest.raises(NormalizationError):
        normalize_relative_date("下周", date(2025, 1, 1))


@pytest.mark.parametrize(
    ("raw", "value", "approximate"),
    [
        ("5000", 5000.0, False),
        ("5000.5", 5000.5, False),
        ("五千", 5000.0, False),
        ("五千左右", 5000.0, True),
        ("两万", 20000.0, False),
        ("十二万左右", 120000.0, True),
    ],
)
def test_normalize_budget(raw, value, approximate):
    assert normalize_budget(raw) == (value, approximate)


@pytest.mark.parametrize("raw", ["", "随便", "0", "-1", "NaN"])
def test_invalid_budget_is_not_guessed(raw):
    with pytest.raises(NormalizationError):
        normalize_budget(raw)


def test_infer_hub_type():
    assert infer_hub_type("杭州萧山机场") == "airport"
    assert infer_hub_type("杭州东站") == "railway_station"
    assert infer_hub_type("杭州火车站") == "railway_station"
    assert infer_hub_type("西湖") is None


def test_time_window_validates_order_days_and_datetime():
    tw = TravelTimeWindow(
        sightseeing_start_date="2025-06-03",
        sightseeing_end_date="2025-06-01",
        sightseeing_days=1,
    )
    tw.arrival.local_datetime = "invalid"
    codes = {issue.code for issue in validate_time_window(tw)}
    assert "invalid_date_order" in codes
    assert "invalid_datetime" in codes


def test_time_window_validates_days_and_hotel_nights():
    tw = TravelTimeWindow(
        sightseeing_start_date="2025-06-01",
        sightseeing_end_date="2025-06-03",
        sightseeing_days=10,
        hotel_check_in_date="2025-06-01",
        hotel_check_out_date="2025-06-03",
        hotel_nights=1,
    )
    codes = {issue.code for issue in validate_time_window(tw)}
    assert "days_mismatch" in codes
    assert "hotel_nights_mismatch" in codes


def test_party_validates_all_fixed_bounds():
    codes = {
        issue.code
        for issue in validate_party(
            adults=-1,
            children=2,
            children_ages=[5, 18],
            seniors=100,
        )
    }
    assert "value_out_of_range" in codes

    mismatch = validate_party(adults=2, children=2, children_ages=[5], seniors=0)
    assert any(issue.code == "children_age_mismatch" for issue in mismatch)
    no_people = validate_party(adults=0, children=0, children_ages=[], seniors=0)
    assert any(issue.code == "no_people" for issue in no_people)
    over_total = validate_party(adults=100, children=1, children_ages=[5], seniors=0)
    assert any(issue.field_path == "party.total_people" for issue in over_total)


def test_budget_rejects_non_finite_and_non_positive_values():
    for value in (-100.0, 0.0, float("nan"), float("inf")):
        assert validate_budget(value)
    assert validate_budget(5000.0) == ()


def test_rooms_validate_count_and_obvious_capacity():
    assert validate_rooms(None, 0) == ()
    assert any(issue.code == "value_out_of_range" for issue in validate_rooms(0, 2))
    assert any(issue.code == "rooms_too_few" for issue in validate_rooms(1, 5))


def test_requirement_text_bounds_are_checked_even_after_unvalidated_assignment():
    req = TravelRequirement(destination="杭州")
    req.destination = "杭" * 65
    req.preferences.must_visit_places = [""]
    paths = {issue.field_path for issue in validate_requirement_bounds(req)}
    assert "destination" in paths
    assert "preferences.must_visit_places" in paths


def test_month_day_normalization_and_invalid_calendar_date():
    assert normalize_relative_date("6月3日", date(2025, 1, 1)) == ("2025-06-03", False)
    with pytest.raises(NormalizationError):
        normalize_relative_date("2月30日", date(2025, 1, 1))


def test_additional_normalization_edges():
    assert normalize_budget("十万") == (100000.0, False)
    with pytest.raises(NormalizationError):
        normalize_budget("一一千")
    assert infer_hub_type("  ") is None


@pytest.mark.parametrize("name,expected", [
    ("上海", None), ("虹桥", None), ("火车站", None), ("高铁站", None),
    ("合肥汽车站", None), ("合肥客运站", None), ("合肥公交站", None), ("合肥地铁站", None),
    ("机场", None), ("上海浦东机场T2航站楼", "airport"), ("合肥南站", "railway_station"),
])
def test_hub_inference_requires_specific_supported_marker(name, expected):
    assert infer_hub_type(name) == expected


def test_time_window_checks_relative_order_and_malformed_dates():
    tw = TravelTimeWindow(
        sightseeing_start_date="2025-06-02",
        sightseeing_end_date="2025-06-03",
        hotel_check_in_date="2025-06-03",
        hotel_check_out_date="2025-06-01",
    )
    tw.arrival.local_datetime = "2025-06-03 09:00"
    tw.departure.local_datetime = "2025-06-02 18:00"
    tw.hotel_nights = -1
    codes = {issue.code for issue in validate_time_window(tw)}
    assert {
        "arrival_after_sightseeing_start",
        "departure_before_sightseeing_end",
        "invalid_hotel_date_order",
        "value_out_of_range",
    } <= codes

    malformed = TravelTimeWindow()
    malformed.sightseeing_start_date = "bad"
    malformed.hotel_check_in_date = "bad"
    paths = {issue.field_path for issue in validate_time_window(malformed)}
    assert "time_window.sightseeing_start_date" in paths
    assert "time_window.hotel_check_in_date" in paths


def test_additional_party_and_list_boundaries():
    zero_children = validate_party(1, 0, [5], 0)
    assert any(issue.code == "children_age_mismatch" for issue in zero_children)
    too_many_ages = validate_party(1, 100, [1] * 101, 0)
    assert any("不能超过100" in issue.message for issue in too_many_ages)
    assert validate_budget(None) == ()

    req = TravelRequirement()
    req.preferences.must_visit_places = ["x"] * 101
    assert any(
        issue.field_path == "preferences.must_visit_places" and "列表项" in issue.message
        for issue in validate_requirement_bounds(req)
    )
