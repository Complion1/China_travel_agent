"""RF-T04: exact public service boundary and complete, source-backed results."""

import asyncio
from uuid import UUID

import pytest

from app.candidate_research import CandidateResearchPlaceObservationArtifact
from app.result_formatting import (
    ResultDayArtifact, ResultFormattingFailed, ResultFormattingLoaded,
    ResultFormattingPrepared, ResultFormattingRequest, ResultFormattingService,
    ResultFormattingUsageError, ResultIndexArtifact, ResultIndexReady,
)
from app.workflow_contracts import (
    ArtifactExpectedModelMismatchError, ArtifactIntegrityError,
    ArtifactNotFoundError, ArtifactReadResult, ArtifactRef,
    ArtifactRegistryConfigurationError, ArtifactRepositoryDependencyError,
    ArtifactRepositoryError, ArtifactRepositoryUsageError, ArtifactTooLargeError,
    DeliveryStatus, TravelSessionState, UnsupportedArtifactTypeError,
    UnsupportedArtifactVersionError,
)
from tests.unit.result_formatting.fakes import (
    RecordingReadRepository, digest, encode, make_read_request, make_registry,
    make_scenario, parsed, replace_itinerary, save_result_days, save_result_index,
    tree,
)


def assert_failed(outcome, code):
    assert type(outcome) is ResultFormattingFailed
    assert outcome.code == code
    assert set(outcome.model_dump()) == {"outcome_type", "schema_version", "code"}


def assert_one_read_per_ref(repository):
    identifiers = [ref.artifact_id for ref, _ in repository.get_calls]
    assert identifiers and len(identifiers) == len(set(identifiers))
    assert repository.forbidden_calls == []


async def saved_bundle(scenario):
    service = ResultFormattingService(artifact_repository=scenario.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days)
    ready = await service.build_index(scenario.request, day_refs=refs)
    assert type(ready) is ResultIndexReady
    index_ref = await save_result_index(scenario, ready.index)
    return prepared, refs, ready.index, index_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("backup_count", [0, 1])
async def test_three_day_optional_backups_roundtrip_without_fabrication(backup_count):
    scenario = await make_scenario(days=3)
    payload = tree(scenario.itinerary)
    days = payload["record"]["draft"]["days"]
    for day in days:
        day["backups"] = day["backups"][:backup_count]
    payload["record"]["summary"]["backups"] = 3 * backup_count
    payload["record"]["summary"]["unknown_hours"] = sum(
        item["business_hours_state"] == "unknown"
        for day in days for item in (*day["visits"], *day["backups"])
    )
    scenario = await replace_itinerary(scenario, payload)
    prepared, _, index, index_ref = await saved_bundle(scenario)
    assert len(prepared.days) == len(index.days) == 3
    assert all(len(day.data.backups) == backup_count for day in prepared.days)
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(
        make_read_request(scenario, index_ref))
    assert type(loaded) is ResultFormattingLoaded
    assert loaded.days == prepared.days


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [1, 60])
async def test_real_repository_roundtrip_external_saves_and_per_call_read_cache(days):
    scenario = await make_scenario(days=days)
    repository = RecordingReadRepository(scenario.repository)
    service = ResultFormattingService(artifact_repository=repository)
    assert repository.get_calls == repository.forbidden_calls == []
    original_state = tree(scenario.request.state)

    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    assert len(prepared.days) == days
    assert_one_read_per_ref(repository)
    first_reads = tuple(repository.get_calls)
    day_refs = await save_result_days(scenario, prepared.days)

    repository.get_calls.clear()
    ready = await service.build_index(scenario.request, day_refs=day_refs)
    assert type(ready) is ResultIndexReady
    assert tuple(entry.ref for entry in ready.index.days) == day_refs
    assert_one_read_per_ref(repository)
    assert repository.get_calls[:len(first_reads)] == list(first_reads)
    index_ref = await save_result_index(scenario, ready.index)

    repository.get_calls.clear()
    loaded = await service.load(make_read_request(scenario, index_ref))
    assert type(loaded) is ResultFormattingLoaded
    assert loaded.index_ref == index_ref and loaded.index == ready.index
    assert loaded.days == prepared.days
    assert loaded.markdown == (
        ready.index.markdown_header + "\n\n"
        + "\n\n".join(day.markdown for day in prepared.days)
        + "\n\n" + ready.index.markdown_footer + "\n"
    )
    assert [ref for ref, _ in repository.get_calls[:days + 1]] == [index_ref, *day_refs]
    assert_one_read_per_ref(repository)
    assert tree(scenario.request.state) == original_state


@pytest.mark.asyncio
async def test_three_day_null_budget_roundtrip_preserves_unverified_costs():
    scenario = await make_scenario(days=3, budget_total_cny=None)
    prepared, refs, index, index_ref = await saved_bundle(scenario)
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(
        make_read_request(scenario, index_ref))
    assert isinstance(loaded, ResultFormattingLoaded)
    assert len(loaded.days) == len(refs) == 3
    assert loaded.days == prepared.days and loaded.index == index
    assert scenario.request.state.requirement_outcome.requirement.budget.total_cny is None
    assert loaded.index.core.requirement.budget_total_cny is None
    assert "用户总预算" not in loaded.markdown
    assert "预算合规" not in loaded.markdown and "剩余预算" not in loaded.markdown
    assert all(f"- {category}：未核验。" in loaded.markdown
               for category in ("住宿", "当地交通", "餐饮", "景点", "其他当地消费"))


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "reversed", "duplicate", "bundle", "markdown", "data"])
async def test_build_index_rejects_incomplete_or_semantically_changed_saved_days(corruption):
    scenario = await make_scenario(days=2)
    service = ResultFormattingService(artifact_repository=scenario.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days)
    if corruption == "missing":
        refs = refs[:1]
    elif corruption == "reversed":
        refs = tuple(reversed(refs))
    elif corruption == "duplicate":
        refs = (refs[0], refs[0])
    else:
        payload = tree(prepared.days[0])
        if corruption == "bundle":
            payload["bundle_digest"] = "sha256:" + "0" * 64
        elif corruption == "markdown":
            payload["markdown"] += "\n伪造的保存正文"
        else:
            payload["data"]["theme"] = "与保存正文不一致的主题"
        changed = parsed(ResultDayArtifact, payload)
        changed_refs = await save_result_days(scenario, (changed,), prefix="changed")
        refs = (*changed_refs, refs[1])
    outcome = await service.build_index(scenario.request, day_refs=refs)
    assert_failed(outcome, "source_mismatch")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["day", "source"])
async def test_load_requires_every_block_and_source_and_stops_at_first_failure(scenario, missing):
    _, day_refs, _, index_ref = await saved_bundle(scenario)
    absent = day_refs[0] if missing == "day" else scenario.request.itinerary_ref
    repository = RecordingReadRepository(scenario.repository, error=ArtifactNotFoundError(), fail_ref=absent)
    outcome = await ResultFormattingService(artifact_repository=repository).load(make_read_request(scenario, index_ref))
    assert_failed(outcome, "artifact_not_found")
    assert repository.get_calls[-1][0] == absent
    assert_one_read_per_ref(repository)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["header", "core", "source_membership"])
async def test_load_rechecks_saved_index_semantics_and_explicit_state_membership(scenario, corruption):
    _, _, index, index_ref = await saved_bundle(scenario)
    if corruption != "source_membership":
        payload = tree(index)
        if corruption == "header":
            payload["markdown_header"] += "\n未经证据支持的文字"
        else:
            payload["core"]["requirement"]["destination"] = "另一城市"
        index = parsed(ResultIndexArtifact, payload)
        index_ref = await save_result_index(scenario, index, logical_step="changed_index")
    request = make_read_request(scenario, index_ref, include_source=corruption != "source_membership")
    assert_failed(await ResultFormattingService(artifact_repository=scenario.repository).load(request), "source_mismatch")


@pytest.mark.asyncio
async def test_unknown_result_schema_is_rejected_before_get_but_invalid_template_uses_registry(scenario):
    _, _, index, index_ref = await saved_bundle(scenario)
    unknown = parsed(ArtifactRef, {**tree(index_ref), "schema_version": "2.0"})
    repository = RecordingReadRepository(scenario.repository)
    service = ResultFormattingService(artifact_repository=repository)
    assert_failed(await service.load(make_read_request(scenario, unknown)), "unsupported_result_version")
    assert repository.get_calls == []

    payload = {**tree(index), "template_version": "unknown-template"}
    canonical_bytes = encode(payload).encode("utf-8")
    malformed_ref = parsed(ArtifactRef, {**tree(index_ref),
        "content_hash": digest(payload), "size_bytes": len(canonical_bytes)})

    def decode_invalid(ref, expected_model):
        # Real Registry verifies these matching bytes and rejects the Literal.
        content = make_registry().decode(ref=ref, canonical_bytes=canonical_bytes, expected_model=expected_model)
        return ArtifactReadResult(ref=ref, content=content)

    repository = RecordingReadRepository(scenario.repository, fail_ref=malformed_ref, replacement=decode_invalid)
    result = await ResultFormattingService(artifact_repository=repository).load(make_read_request(scenario, malformed_ref))
    assert_failed(result, "artifact_integrity_error")
    assert len(repository.get_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["not_member", "cross_run", "not_ready", "unknown_source_version"])
async def test_input_business_preconditions_fail_before_io(scenario, invalid):
    state, ref = scenario.request.state, scenario.request.itinerary_ref
    expected = "source_mismatch"
    if invalid == "not_member":
        state = TravelSessionState.model_validate({**state.model_dump(mode="python"), "artifact_refs": ()}, strict=True)
    elif invalid == "cross_run":
        payload = tree(ref)
        payload["operation_id"]["run_id"] = str(UUID(int=999))
        ref = parsed(ArtifactRef, payload)
    elif invalid == "not_ready":
        state = TravelSessionState.model_validate({**state.model_dump(mode="python"),
            "delivery_status": DeliveryStatus.AWAITING_USER}, strict=True)
        expected = "input_not_ready"
    else:
        ref = parsed(ArtifactRef, {**tree(ref), "schema_version": "2.0"})
        state = TravelSessionState.model_validate({**state.model_dump(mode="python"), "artifact_refs": (ref,)}, strict=True)
        expected = "artifact_contract_error"
    request = ResultFormattingRequest(schema_version="1.0", state=state, itinerary_ref=ref)
    repository = RecordingReadRepository(scenario.repository)
    assert_failed(await ResultFormattingService(artifact_repository=repository).prepare(request), expected)
    assert repository.get_calls == []


@pytest.mark.asyncio
async def test_valid_incomplete_itinerary_is_not_formatted(scenario):
    payload = tree(scenario.itinerary)
    payload["record"].update(record_type="incomplete", stop_reasons=["no_progress"], gaps=[{
        "schema_version": "1.0", "summary_type": "planning_process", "gap_id": "no-progress",
        "evidence_indexes": [], "process_reason": "no_progress",
    }])
    changed = await replace_itinerary(scenario, payload)
    repository = RecordingReadRepository(changed.repository)
    outcome = await ResultFormattingService(artifact_repository=repository).prepare(changed.request)
    assert_failed(outcome, "itinerary_not_completed")
    assert len(repository.get_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("error_type", "code"), [
    (ArtifactNotFoundError, "artifact_not_found"),
    (ArtifactIntegrityError, "artifact_integrity_error"),
    (UnsupportedArtifactTypeError, "artifact_contract_error"),
    (UnsupportedArtifactVersionError, "artifact_contract_error"),
    (ArtifactExpectedModelMismatchError, "artifact_contract_error"),
    (ArtifactRegistryConfigurationError, "artifact_contract_error"),
    (ArtifactRepositoryUsageError, "artifact_contract_error"),
    (ArtifactRepositoryDependencyError, "artifact_dependency_error"),
    (ArtifactTooLargeError, "result_too_large"),
    (ArtifactRepositoryError, "artifact_contract_error"),
    (RuntimeError, "internal_error"),
])
async def test_repository_errors_are_safe_and_never_retried(scenario, error_type, code):
    error = RuntimeError("secret-source-text") if error_type is RuntimeError else error_type()
    repository = RecordingReadRepository(scenario.repository, error=error)
    outcome = await ResultFormattingService(artifact_repository=repository).prepare(scenario.request)
    assert_failed(outcome, code)
    assert "secret-source-text" not in outcome.model_dump_json() + repr(outcome)
    assert len(repository.get_calls) == 1
    assert repository.forbidden_calls == []


@pytest.mark.asyncio
async def test_cancellation_propagates_without_followup_reads(scenario):
    error = asyncio.CancelledError()
    repository = RecordingReadRepository(scenario.repository, error=error)
    with pytest.raises(asyncio.CancelledError) as caught:
        await ResultFormattingService(artifact_repository=repository).prepare(scenario.request)
    assert caught.value is error
    assert len(repository.get_calls) == 1


@pytest.mark.asyncio
async def test_request_fresh_copy_precedes_first_repository_await(scenario):
    entered, resume = asyncio.Event(), asyncio.Event()

    class PausingRepository(RecordingReadRepository):
        async def get(self, ref, *, expected_model):
            if not self.get_calls:
                entered.set()
                await resume.wait()
            return await super().get(ref, expected_model=expected_model)

    repository = PausingRepository(scenario.repository)
    service = ResultFormattingService(artifact_repository=repository)
    task = asyncio.create_task(service.prepare(scenario.request))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        scenario.request.state.requirement_outcome.requirement.budget.total_cny = 5.0
        resume.set()
        result = await task
        assert type(result) is ResultFormattingPrepared
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_m08_observation_content_run_must_match_its_saved_ref(scenario):
    ref = scenario.refs["candidate_details"]
    payload = tree(scenario.evidence[ref.artifact_id].content)
    payload["run_id"] = str(UUID(int=999))
    observation = parsed(CandidateResearchPlaceObservationArtifact, payload)
    repository = RecordingReadRepository(scenario.repository, fail_ref=ref,
        replacement=lambda requested, expected: ArtifactReadResult(ref=requested, content=observation))
    result = await ResultFormattingService(artifact_repository=repository).prepare(scenario.request)
    assert_failed(result, "source_mismatch")
    assert repository.get_calls[-1][0] == ref


@pytest.mark.asyncio
async def test_business_evidence_index_outside_directory_is_source_mismatch(scenario):
    payload = tree(scenario.itinerary)
    payload["record"]["draft"]["days"][0]["visits"][0]["business_evidence_indexes"] = [127]
    changed = await replace_itinerary(scenario, payload)
    result = await ResultFormattingService(artifact_repository=changed.repository).prepare(changed.request)
    assert_failed(result, "source_mismatch")


@pytest.mark.asyncio
async def test_usage_errors_precede_io_and_do_not_expose_payload(scenario):
    repository = RecordingReadRepository(scenario.repository)
    service = ResultFormattingService(artifact_repository=repository)
    for value in ({"secret": "secret-source-text"}, scenario.request.model_copy(update={"schema_version": "bad"})):
        with pytest.raises(ResultFormattingUsageError) as caught:
            await service.prepare(value)
        assert str(caught.value) == repr(caught.value) == "ResultFormattingUsageError"
        assert caught.value.__cause__ is None
    with pytest.raises(ResultFormattingUsageError):
        await service.build_index(scenario.request, day_refs=[])
    assert repository.get_calls == []


@pytest.mark.asyncio
async def test_same_id_with_different_seven_field_ref_is_not_a_cache_hit(scenario):
    service = ResultFormattingService(artifact_repository=scenario.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    refs = await save_result_days(scenario, prepared.days)
    alias = parsed(ArtifactRef, {**tree(refs[0]),
        "artifact_id": str(scenario.request.itinerary_ref.artifact_id)})
    repository = RecordingReadRepository(scenario.repository)
    result = await ResultFormattingService(artifact_repository=repository).build_index(
        scenario.request, day_refs=(alias,),
    )
    assert_failed(result, "source_mismatch")
    assert alias not in [ref for ref, _ in repository.get_calls]
    assert_one_read_per_ref(repository)
