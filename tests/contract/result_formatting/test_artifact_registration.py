"""真实 M04 Registry 对 M12 的严格图、规范字节和 helper 的验证。"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.result_formatting import (
    RESULT_FORMATTING_ARTIFACT_REGISTRATIONS,
    ResultDayArtifact,
    ResultFormattingUsageError,
    ResultIndexArtifact,
    build_result_artifact_write_request,
)
from app.result_formatting.artifacts import (
    ResultCoordinate,
    ResultCore,
    ResultDayData,
    ResultDayEntry,
    ResultEndpoint,
    ResultInterval,
    ResultPlace,
    ResultRequirement,
    ResultRoute,
    ResultSource,
    ResultTransfer,
    fresh_model,
    validate_artifact_size,
)
from app.workflow_contracts import (
    ArtifactRef,
    ArtifactSchemaRegistry,
    ArtifactTooLargeError,
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
    InMemoryArtifactRepository,
    OperationId,
    WorkflowPhase,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 9, 26, tzinfo=timezone.utc)
LOCAL_DATE = date(2026, 9, 26)
DIGEST = "sha256:" + "1" * 64


def operation(step, phase=WorkflowPhase.OUTPUT_FORMATTING):
    return OperationId(run_id=RUN_ID, phase=phase, logical_step=step)


def ref(number, artifact_type, phase=WorkflowPhase.ITINERARY_PLANNING):
    return ArtifactRef(
        artifact_id=UUID(int=number), artifact_type=artifact_type, schema_version="1.0",
        content_hash=DIGEST, operation_id=operation(f"source_{number}", phase),
        created_at=UTC, size_bytes=1,
    )


def make_day(markdown="合成的一日攻略"):
    return ResultDayArtifact(
        schema_version="1.0", template_version="zh-guide-v1", run_id=RUN_ID,
        source_itinerary_ref=ref(100, "itinerary_planning.itinerary"),
        input_digest=DIGEST, bundle_digest=DIGEST,
        data=ResultDayData(
            day_no=1, date=LOCAL_DATE, day_kind="transit_only", theme="交通日",
            open_intervals=(), visits=(), route_legs=(), backups=(),
        ),
        markdown=markdown,
    )


def make_core():
    arrival = ResultEndpoint(hub_name="合成车站", hub_type="railway_station", local_datetime="2026-09-26 08:00", is_approximate=False)
    departure = ResultEndpoint(hub_name="合成车站", hub_type="railway_station", local_datetime="2026-09-26 19:00", is_approximate=False)
    requirement = ResultRequirement(
        destination="合成城市", arrival=arrival, departure=departure,
        sightseeing_start_date=LOCAL_DATE, sightseeing_end_date=LOCAL_DATE, sightseeing_days=1,
        hotel_check_in_date=LOCAL_DATE, hotel_check_out_date=LOCAL_DATE + timedelta(days=1),
        hotel_nights=1, adults_count=1, children_count=0, children_ages=(), seniors_count=0,
        total_people=1, room_count=1, budget_total_cny=1000.0, budget_is_approximate=False,
        budget_scope="destination_only", mobility_constraints=None, dietary_restrictions=None,
        room_type_requirements=None, hotel_preferences=None, transport_preferences=None,
        daily_time_preferences=None, free_text_notes=None, must_visit_places=(), avoid_places=(),
    )
    coordinate = ResultCoordinate(coordinate_system="bd09ll", latitude=30.0, longitude=120.0)
    places = tuple(ResultPlace(
        place_id=number, kind=kind, uid="synthetic-hotel" if number == 1 else None,
        name="合成地点", address="合成地址", coordinate=coordinate,
        identity_source_id=number, business_hours=(), business_hours_source_id=None,
    ) for number, kind in enumerate(("hotel", "arrival_hub", "departure_hub"), start=1))
    source_specs = (
        (1, "hotel.place_search", WorkflowPhase.HOTEL_SELECTION, "place_search"),
        (2, "itinerary_planning.hub_geocode_observation", WorkflowPhase.ITINERARY_PLANNING, "geocoding"),
        (3, "itinerary_planning.hub_geocode_observation", WorkflowPhase.ITINERARY_PLANNING, "geocoding"),
        (4, "itinerary_planning.directions_observation", WorkflowPhase.ITINERARY_PLANNING, "directions"),
        (5, "itinerary_planning.directions_observation", WorkflowPhase.ITINERARY_PLANNING, "directions"),
    )
    sources = tuple(ResultSource(
        source_id=number, artifact_ref=ref(number, artifact_type, phase), source="baidu_maps_mcp",
        capability=capability, retrieved_at_utc=UTC, verified=True,
    ) for number, artifact_type, phase, capability in source_specs)
    interval = ResultInterval(start_at_utc=UTC, end_at_utc=UTC, timezone="Asia/Shanghai")
    def route(name, origin, destination, source):
        return ResultRoute(
            leg_id=name, origin_place_id=origin, destination_place_id=destination, interval=interval,
            transport_semantic="walking", route_mode="walking", distance_m=0, duration_s=0,
            scheduled_minutes=0, source_id=source, alternative_index=0,
        )
    return ResultCore(
        requirement=requirement, effective_pace="标准", places=places, sources=sources,
        arrival_transfer=ResultTransfer(transfer_kind="arrival", hub_place_id=2, hotel_place_id=1,
            route=route("arrival", 2, 1, 4), processing_minutes=30, approximate_extra_minutes=0),
        departure_transfer=ResultTransfer(transfer_kind="departure", hub_place_id=3, hotel_place_id=1,
            route=route("departure", 1, 3, 5), processing_minutes=30, approximate_extra_minutes=0),
        unknown_business_visit_count=0, unknown_business_backup_count=0,
        weather_status="not_collected", map_status="deferred", cost_status="unverified", scope_notice_code="m09_first_release",
    )


def make_index(day_ref):
    return ResultIndexArtifact(
        schema_version="1.0", template_version="zh-guide-v1", run_id=RUN_ID,
        source_itinerary_ref=ref(100, "itinerary_planning.itinerary"),
        input_digest=DIGEST, bundle_digest=DIGEST, core=make_core(),
        days=(ResultDayEntry(day_no=1, date=LOCAL_DATE, ref=day_ref),),
        markdown_header="合成攻略开头", markdown_footer="合成来源声明",
    )


class FixedClock:
    def now(self):
        return UTC


class SequenceUUIDFactory:
    def __init__(self):
        self.value = 1000

    def new(self):
        self.value += 1
        return UUID(int=self.value)


@pytest.mark.asyncio
async def test_both_artifacts_round_trip_through_real_registry_and_repository():
    registry = ArtifactSchemaRegistry(RESULT_FORMATTING_ARTIFACT_REGISTRATIONS)
    assert tuple((r.artifact_type, r.schema_version, r.model) for r in RESULT_FORMATTING_ARTIFACT_REGISTRATIONS) == (
        ("result_formatting.day", "1.0", ResultDayArtifact),
        ("result_formatting.index", "1.0", ResultIndexArtifact),
    )
    repository = InMemoryArtifactRepository(registry=registry, utc_clock=FixedClock(),
        uuid_factory=SequenceUUIDFactory(), limits=DEMO_ARTIFACT_REPOSITORY_LIMITS)
    day = make_day()
    day_request = build_result_artifact_write_request(operation_id=operation("day_1"), artifact=day)
    prepared = registry.prepare(day_request)
    assert prepared.size_bytes == len(prepared.canonical_bytes)
    assert prepared.size_bytes > len(prepared.canonical_bytes.decode("utf-8"))
    assert registry.prepare(day_request) == prepared
    day_ref = await repository.put(day_request)
    day_read = await repository.get(day_ref, expected_model=ResultDayArtifact)
    assert day_read.content == day and day_read.content is not day
    assert day_read.content.data is not day.data
    index = make_index(day_ref)
    index_request = build_result_artifact_write_request(operation_id=operation("result_index"), artifact=index)
    index_ref = await repository.put(index_request)
    index_read = await repository.get(index_ref, expected_model=ResultIndexArtifact)
    assert index_read.content == index and index_read.content is not index
    assert index_read.content.core is not index.core


@pytest.mark.parametrize("mutation", ["nested_bool", "tuple_to_list", "literal_bool", "wrong_source_run"])
def test_helper_rejects_forged_python_tree_before_json(mutation):
    day = make_day()
    if mutation == "nested_bool":
        object.__setattr__(day.data, "day_no", True)
    elif mutation == "tuple_to_list":
        object.__setattr__(day.data, "visits", [])
    elif mutation == "literal_bool":
        day = make_index(ref(90, "result_formatting.day", WorkflowPhase.OUTPUT_FORMATTING))
        object.__setattr__(day.core.sources[0], "verified", 1)
    else:
        object.__setattr__(day.source_itinerary_ref.operation_id, "run_id", UUID(int=42))
    with pytest.raises(ResultFormattingUsageError) as caught:
        build_result_artifact_write_request(operation_id=operation("save"), artifact=day)
    assert str(caught.value) == repr(caught.value) == "ResultFormattingUsageError"
    assert caught.value.__cause__ is None


def test_helper_rejects_operation_scope_and_index_day_collision():
    day = make_day()
    for bad_operation in (operation("save", WorkflowPhase.ITINERARY_PLANNING), object()):
        with pytest.raises(ResultFormattingUsageError):
            build_result_artifact_write_request(operation_id=bad_operation, artifact=day)
    day_ref = ref(90, "result_formatting.day", WorkflowPhase.OUTPUT_FORMATTING)
    with pytest.raises(ResultFormattingUsageError):
        build_result_artifact_write_request(operation_id=day_ref.operation_id, artifact=make_index(day_ref))
    with pytest.raises(ResultFormattingUsageError):
        build_result_artifact_write_request(operation_id=operation("save"), artifact={})


def test_capacity_uses_utf8_canonical_bytes_and_no_io_preflight():
    validate_artifact_size(make_day())
    # Character count is below 1 MiB; UTF-8 content itself exceeds the limit.
    too_large = make_day("中" * 350_000)
    assert len(too_large.markdown) < 1_048_576
    with pytest.raises(ArtifactTooLargeError):
        validate_artifact_size(too_large)
    with pytest.raises(ArtifactTooLargeError):
        build_result_artifact_write_request(operation_id=operation("day_large"), artifact=too_large)


def test_fresh_copy_is_independent_and_repr_omits_payload():
    day = make_day()
    fresh = fresh_model(day, ResultDayArtifact)
    assert fresh == day and fresh is not day and fresh.data is not day.data
    assert "合成" not in repr(day) and "交通日" not in repr(day.data)
    object.__setattr__(day.data, "theme", "后续修改")
    assert fresh.data.theme == "交通日"
