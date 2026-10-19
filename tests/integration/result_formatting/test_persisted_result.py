"""RF-PERSIST: real PostgreSQL boundaries, without claiming M11 recovery."""

import hashlib
from uuid import UUID

import pytest

from app.result_formatting import (
    ResultDayArtifact, ResultFormattingFailed, ResultFormattingLoaded,
    ResultFormattingPrepared, ResultFormattingService, ResultIndexReady,
    ResultReadRequest, build_result_artifact_write_request,
)
from app.workflow_contracts import ArtifactAbsent, ArtifactConflictError, ArtifactFound, ArtifactWriteRequest
from tests.integration.result_formatting.restart_worker import (
    checked_canonical, operation, result_manifest,
)
from tests.unit.result_formatting.fakes import (
    make_read_request, make_registry, make_scenario, parsed, save_result_days, save_result_index, tree,
)


def checked_itinerary_canonical(artifact, ref):
    """核对当前分钟记录的规范字节，不借助结果写入方。"""
    assert ref.artifact_type == "itinerary_planning.itinerary"
    assert artifact.schema_version == ref.schema_version
    prepared = make_registry().prepare(ArtifactWriteRequest(
        operation_id=ref.operation_id, artifact_type=ref.artifact_type,
        schema_version=ref.schema_version, content=tree(artifact),
    ))
    assert prepared.content_hash == ref.content_hash
    assert prepared.size_bytes == ref.size_bytes
    assert "sha256:" + hashlib.sha256(prepared.canonical_bytes).hexdigest() == ref.content_hash
    return prepared.canonical_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("with_transit_steps", [False, True])
@pytest.mark.parametrize("minute_schedule", [False, True])
@pytest.mark.parametrize("planning_version", ["6.0", "7.0", "8.0"])
async def test_complete_result_reopens_with_same_canonical_bytes(runtime_factory, database_snapshot, with_transit_steps, minute_schedule, planning_version):
    run_id = UUID(int=UUID("12000000-0000-4000-8000-00000000c201").int
                  + (16 if with_transit_steps else 0) + (1 if minute_schedule else 0) + ({"6.0": 0, "7.0": 32, "8.0": 64}[planning_version]))
    runtime = await runtime_factory()
    instructions = ("步行到合成甲站", "乘坐合成1号线至乙站", "换乘合成20路至丙站") if with_transit_steps else None
    scenario = await make_scenario(repository=runtime.repository, days=3, run_id=run_id,
                                   transit_instructions=instructions, minute_schedule=minute_schedule, planning_version=planning_version)
    if planning_version in {"7.0", "8.0"}:
        from tests.unit.result_formatting.fakes import replace_itinerary, interval, local_time, START_DATE
        payload = tree(scenario.itinerary)
        payload["record"]["draft"]["days"][0]["planned_breaks"] = [dict(break_id="rest", kind="rest", reason="午后休息",
            interval=interval(local_time(START_DATE, 16), local_time(START_DATE, 16, 30)))]
        payload["record"]["draft"]["days"][0]["quality_issues"] = [dict(date=START_DATE.isoformat(), rule="unexplained_gap",
            interval=interval(local_time(START_DATE, 13), local_time(START_DATE, 16)), minutes=180)]
        original_itinerary_ref = scenario.request.itinerary_ref
        scenario = await replace_itinerary(scenario, payload)
        scenario.refs["pre_break_itinerary"] = original_itinerary_ref
    service = ResultFormattingService(artifact_repository=runtime.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days)
    ready = await service.build_index(scenario.request, day_refs=refs)
    assert type(ready) is ResultIndexReady
    index_ref = await save_result_index(scenario, ready.index)
    request_json = make_read_request(scenario, index_ref).model_dump_json()
    first = await service.load(ResultReadRequest.model_validate_json(request_json, strict=True))
    assert type(first) is ResultFormattingLoaded
    if minute_schedule:
        assert "11:37" in first.markdown and "13:12" in first.markdown
        for day in scenario.itinerary.record.draft.days:
            assert day.visits[0].interval.schema_version == "2.0"
            for route in day.route_legs:
                assert route.scheduled_minutes == ((route.duration_s + 599) // 600) * 10 == 20
    if with_transit_steps:
        assert "1. 步行到合成甲站\n  2. 乘坐合成1号线至乙站\n  3. 换乘合成20路至丙站" in first.markdown
        assert "未选中方案" not in first.markdown
    before_manifest = result_manifest(first, mode="reopen")
    original_bytes = (checked_canonical(first.index, first.index_ref), *(
        checked_canonical(day, entry.ref) for day, entry in zip(first.days, first.index.days, strict=True)
    ))
    before_database = await database_snapshot(run_id)
    expected_refs = (*scenario.refs.values(), *refs, index_ref)
    source_ref = scenario.request.itinerary_ref
    source_before = checked_itinerary_canonical(scenario.itinerary, source_ref)
    assert len(before_database["records"]) == len({ref.artifact_id for ref in expected_refs})
    for ref in expected_refs:
        stored = before_database["records"][str(ref.artifact_id)]
        assert stored["stored_hash"] == stored["content_hash"] == ref.content_hash
        assert stored["stored_size"] == stored["size_bytes"] == ref.size_bytes
    await runtime.close()

    restarted = await runtime_factory()
    found = await restarted.repository.lookup(operation_id=index_ref.operation_id)
    assert isinstance(found, ArtifactFound) and found.ref == index_ref
    # JSON and the stable lookup contract cross the runtime boundary; no old
    # service, repository cache, or in-memory artifact handle is reused.
    second = await ResultFormattingService(artifact_repository=restarted.repository).load(
        ResultReadRequest.model_validate_json(request_json, strict=True),
    )
    assert type(second) is ResultFormattingLoaded
    assert result_manifest(second, mode="reopen") == before_manifest
    reopened_bytes = (checked_canonical(second.index, second.index_ref), *(
        checked_canonical(day, entry.ref) for day, entry in zip(second.days, second.index.days, strict=True)
    ))
    if reopened_bytes != original_bytes:
        pytest.fail("M12_REOPEN_CANONICAL_BYTES_CHANGED", pytrace=False)
    source_after = await restarted.repository.get(source_ref, expected_model=type(scenario.itinerary))
    assert source_after.ref == source_ref
    assert checked_itinerary_canonical(source_after.content, source_ref) == source_before
    assert source_after.content.schema_version == planning_version
    if planning_version in {"7.0", "8.0"}:
        assert "午后休息" in second.markdown
        assert len(source_after.content.record.draft.days[0].planned_breaks) == 1
        assert source_after.content.record.draft.days[0].quality_issues[0].minutes == 180
        assert second.index.core.quality_issue_count == 1
        assert "未解释" in second.markdown or "空档" in second.markdown
    assert await database_snapshot(run_id) == before_database


@pytest.mark.asyncio
async def test_exact_replay_and_conflict_never_add_or_overwrite_rows(runtime_factory, database_snapshot):
    run_id = UUID("12000000-0000-4000-8000-00000000c102")
    runtime = await runtime_factory()
    scenario = await make_scenario(repository=runtime.repository, days=2, run_id=run_id)
    service = ResultFormattingService(artifact_repository=runtime.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days)
    ready = await service.build_index(scenario.request, day_refs=refs)
    assert type(ready) is ResultIndexReady
    index_ref = await save_result_index(scenario, ready.index)
    baseline = await database_snapshot(run_id)
    assert await save_result_days(scenario, prepared.days) == refs
    assert await save_result_index(scenario, ready.index) == index_ref
    assert await database_snapshot(run_id) == baseline

    changed = parsed(ResultDayArtifact, {**tree(prepared.days[0]), "markdown": "合成冲突正文"})
    with pytest.raises(ArtifactConflictError):
        await runtime.repository.put(build_result_artifact_write_request(
            operation_id=refs[0].operation_id, artifact=changed,
        ))
    assert await database_snapshot(run_id) == baseline
    preserved = await runtime.repository.get(refs[0], expected_model=ResultDayArtifact)
    assert preserved.ref == refs[0]
    if checked_canonical(preserved.content, preserved.ref) != checked_canonical(prepared.days[0], refs[0]):
        pytest.fail("M12_CONFLICT_OVERWROTE_CANONICAL_BYTES", pytrace=False)
    loaded = await service.load(make_read_request(scenario, index_ref))
    assert type(loaded) is ResultFormattingLoaded


@pytest.mark.asyncio
async def test_partial_save_has_no_publishable_index_and_survives_reopen(runtime_factory, database_snapshot):
    run_id = UUID("12000000-0000-4000-8000-00000000c103")
    runtime = await runtime_factory()
    scenario = await make_scenario(repository=runtime.repository, days=2, run_id=run_id)
    service = ResultFormattingService(artifact_repository=runtime.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days[:1])
    index_operation = operation(run_id, "result_index")
    assert isinstance(await runtime.repository.lookup(operation_id=index_operation), ArtifactAbsent)
    refused = await service.build_index(scenario.request, day_refs=refs)
    assert type(refused) is ResultFormattingFailed and refused.code == "source_mismatch"
    # A real saved day Ref cannot be promoted to a complete-result root.
    refused_read = await service.load(make_read_request(scenario, refs[0]))
    assert type(refused_read) is ResultFormattingFailed
    assert refused_read.code == "artifact_contract_error"
    before = await database_snapshot(run_id)
    result_types = [item["type"] for item in before["records"].values() if item["type"].startswith("result_formatting.")]
    assert result_types == ["result_formatting.day"]
    await runtime.close()

    restarted = await runtime_factory()
    assert isinstance(await restarted.repository.lookup(operation_id=index_operation), ArtifactAbsent)
    saved_day = await restarted.repository.get(refs[0], expected_model=ResultDayArtifact)
    checked_canonical(saved_day.content, refs[0])
    assert await database_snapshot(run_id) == before
