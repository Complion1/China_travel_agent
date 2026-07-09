from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class TravelPace(str, Enum):
    RELAXED = "relaxed"
    NORMAL = "normal"
    INTENSE = "intense"


class PlaceRole(str, Enum):
    HOTEL = "hotel"
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    TRANSPORT_HUB = "transport_hub"
    AREA = "area"
    UNKNOWN = "unknown"


class TransportMode(str, Enum):
    PUBLIC_TRANSIT = "public_transit"
    TAXI = "taxi"
    DRIVING = "driving"
    WALKING = "walking"
    CYCLING = "cycling"
    MIXED = "mixed"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Coordinates:
    lat: float | None = None
    lng: float | None = None
    coord_type: str = "gcj02"

    @classmethod
    def from_raw(cls, value: Any) -> "Coordinates | None":
        if not value:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and "," in value:
            lat, lng = value.split(",", 1)
            return cls(_safe_float(lat), _safe_float(lng))
        if isinstance(value, dict):
            lat = value.get("lat") or value.get("latitude") or value.get("y")
            lng = value.get("lng") or value.get("lon") or value.get("longitude") or value.get("x")
            return cls(_safe_float(lat), _safe_float(lng))
        return None

    def to_location_param(self) -> str | None:
        if self.lat is None or self.lng is None:
            return None
        return f"{self.lat:.6f},{self.lng:.6f}"


@dataclass
class TravelRequirement:
    city: str | None = None
    days: int | None = None
    styles: list[str] = field(default_factory=list)
    arrival_place: str | None = None
    arrival_time: str | None = None
    departure_place: str | None = None
    departure_time: str | None = None
    hotel_preference: str | None = None
    transport_preference: str | None = None
    pace: str = TravelPace.NORMAL.value
    people: str | None = None
    budget: str | None = None


@dataclass
class BaiduPlaceRequest:
    user_raw_request: str
    region: str
    sort: str = "relevance"
    center: Coordinates | None = None


@dataclass
class BaiduGeocodingRequest:
    address: str
    region: str | None = None


@dataclass
class BaiduDirectionRequest:
    user_raw_request: str
    location: Coordinates
    refer_pois: str | None = None


@dataclass
class BaiduWeatherRequest:
    region: str | None = None
    location: Coordinates | None = None


@dataclass
class PlaceCandidate:
    name: str
    role: str = PlaceRole.UNKNOWN.value
    address: str | None = None
    uid: str | None = None
    location: Coordinates | None = None
    category: str | None = None
    source: str = "baidu_place"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_baidu(cls, item: dict[str, Any], role: str = PlaceRole.UNKNOWN.value) -> "PlaceCandidate":
        location = Coordinates.from_raw(item.get("location") or item.get("point"))
        return cls(
            name=str(item.get("name") or item.get("title") or item.get("std_tag") or "未命名地点"),
            role=role,
            address=item.get("address") or item.get("addr"),
            uid=item.get("uid"),
            location=location,
            category=item.get("std_tag") or item.get("tag") or item.get("category"),
            raw=item,
        )


@dataclass
class WeatherSnapshot:
    region: str | None = None
    summary: str | None = None
    source: str = "baidu_weather"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteCandidate:
    name: str
    origin: str | None = None
    destination: str | None = None
    mode: str = TransportMode.MIXED.value
    summary: str | None = None
    duration_minutes: int | None = None
    distance_meters: int | None = None
    resource_key: str | None = None
    source: str = "baidu_direction"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DayPlanItem:
    title: str
    item_type: str
    place: PlaceCandidate | None = None
    start_time: str | None = None
    end_time: str | None = None
    route_to_next: RouteCandidate | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class DayPlan:
    day: int
    title: str | None = None
    items: list[DayPlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ItineraryDraft:
    city: str | None = None
    days: int | None = None
    selected_hotel: PlaceCandidate | None = None
    day_plans: list[DayPlan] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    field: str | None = None


@dataclass
class ValidationReport:
    ok: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        return [
            issue.message
            for issue in self.issues
            if issue.severity in {ValidationSeverity.WARNING.value, ValidationSeverity.ERROR.value}
        ]

    def add(self, severity: str, code: str, message: str, field: str | None = None) -> None:
        self.issues.append(ValidationIssue(severity=severity, code=code, message=message, field=field))
        if severity == ValidationSeverity.ERROR.value:
            self.ok = False


@dataclass
class ToolCallRecord:
    tool: str
    tool_input: dict[str, Any]
    ok: bool
    output_summary: str = ""
    error: str | None = None
    raw_output: dict[str, Any] | None = None


@dataclass
class TravelPlanState:
    user_input: str
    requirement: TravelRequirement = field(default_factory=TravelRequirement)
    missing_fields: list[str] = field(default_factory=list)
    clarify_question: str | None = None
    weather: WeatherSnapshot | dict[str, Any] | None = None
    hotel_candidates: list[PlaceCandidate] = field(default_factory=list)
    attractions: list[PlaceCandidate] = field(default_factory=list)
    restaurants: list[PlaceCandidate] = field(default_factory=list)
    routes: list[RouteCandidate] = field(default_factory=list)
    itinerary: ItineraryDraft | None = None
    validation_report: ValidationReport = field(default_factory=ValidationReport)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_answer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_plain(
            {
                "user_input": self.user_input,
                "requirement": self.requirement,
                "missing_fields": self.missing_fields,
                "clarify_question": self.clarify_question,
                "weather": self.weather,
                "hotel_candidates": self.hotel_candidates,
                "attractions": self.attractions,
                "restaurants": self.restaurants,
                "routes": self.routes,
                "itinerary": self.itinerary,
                "validation_report": self.validation_report,
                "tool_calls": self.tool_calls,
                "final_answer": self.final_answer,
            }
        )


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: to_plain(val) for key, val in value.__dict__.items()}
    if isinstance(value, dict):
        return {str(key): to_plain(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
