from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.candidate_research import (
    CandidateResearchInitialRequest, CandidateResearchOperationPlan,
    CandidateCountShortfallGap, AvoidResolutionMissingGap, CandidateAnchorRef, HotelAnchorRef, ResearchRole, SearchAreaMode, ResearchObjectiveKind,
)
from app.candidate_research.fingerprints import (
    action_fingerprint, build_objective_id, candidate_handle, canonical_json,
    details_action_fingerprint, evidence_handle, invocation_request_digest,
    objective_fingerprint, objective_handle, register_fingerprint, search_action_fingerprint,
    requirement_group_id, requirement_item_id,
    supplemental_gap_fingerprint,
)
import app.candidate_research.fingerprints as fingerprint_module
from app.candidate_research.models import ProposedDetailsAction
from app.requirements import ConfirmedTravelRequirement, FieldMetadataRegistry, TravelRequirement, InterestTag
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


def _ref(run_id):
    return ArtifactRef(
        artifact_id=uuid4(), artifact_type="hotel.selection", schema_version="1.0",
        content_hash="sha256:" + "c" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )


def _request(run_id):
    slots = tuple({"invocation_slot_index": index, "action_operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"a{index}"), "judgment_operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"j{index}"), "candidate_set_operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"c{index}"), "place_observation_operation_ids": (), "action_attempt_operation_ids": tuple(OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"a{index}_attempt_{j}") for j in range(3)), "judgment_attempt_operation_ids": tuple(OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"j{index}_attempt_{j}") for j in range(3))} for index in range(6))
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=slots, terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="terminal"))
    confirmed = ConfirmedTravelRequirement(schema_version="1.0", requirement=TravelRequirement(), metadata_without_raw_evidence=FieldMetadataRegistry(), confirmation_summary_digest="confirmed")
    return CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=_ref(run_id), operation_plan=plan)


def test_search_details_and_action_fingerprints_follow_exact_tree():
    first = search_action_fingerprint(query=" museum ", tag=None, area_mode=SearchAreaMode.CITY, destination="杭州")
    second = search_action_fingerprint(query="museum", tag=None, area_mode=SearchAreaMode.CITY, destination="杭州")
    assert first == second
    assert search_action_fingerprint(query="museum", tag="历史", area_mode=SearchAreaMode.CITY, destination="杭州") != first
    nearby = search_action_fingerprint(query="museum", tag=None, area_mode=SearchAreaMode.NEARBY, anchor=CandidateAnchorRef(uid="u1"), radius_km=1)
    assert nearby != first
    assert details_action_fingerprint(uid="u1") != details_action_fingerprint(uid="u2")
    assert action_fingerprint(ProposedDetailsAction(ordinal=0, objective_id="o", uid="u1")) == details_action_fingerprint("u1")
    with pytest.raises(ValueError):
        search_action_fingerprint(query=" ", tag=None, area_mode=SearchAreaMode.CITY, destination="杭州")


def test_gap_fingerprint_ignores_identity_but_keeps_semantics():
    common = dict(confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64)
    run_id = uuid4()
    ref = ArtifactRef(artifact_id=uuid4(), artifact_type="x.y", schema_version="1.0", content_hash="sha256:" + "c" * 64, operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="s"), created_at=datetime.now(timezone.utc), size_bytes=1)
    left = CandidateCountShortfallGap(gap_id="g1", evidence_refs=(ref,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1)
    right = left.model_copy(update={"gap_id": "g2"})
    assert supplemental_gap_fingerprint(**common, gap=left) == supplemental_gap_fingerprint(**common, gap=right)
    changed = left.model_copy(update={"target_count": 2})
    assert supplemental_gap_fingerprint(**common, gap=left) != supplemental_gap_fingerprint(**common, gap=changed)


def test_gap_fingerprint_uses_typed_target_not_current_count_or_evidence_identity():
    common = dict(confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64)
    run_id = uuid4()
    def ref(step: str, artifact_id):
        return ArtifactRef(artifact_id=artifact_id, artifact_type="x.y", schema_version="1.0", content_hash="sha256:" + "c" * 64, operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=step), created_at=datetime.now(timezone.utc), size_bytes=1)
    first = CandidateCountShortfallGap(gap_id="g1", evidence_refs=(ref("s1", uuid4()),), role=ResearchRole.ATTRACTION, current_count=0, target_count=2)
    same_target = CandidateCountShortfallGap(gap_id="g2", evidence_refs=(ref("s2", uuid4()),), role=ResearchRole.ATTRACTION, current_count=1, target_count=2)
    changed_target = CandidateCountShortfallGap(gap_id="g3", evidence_refs=(ref("s3", uuid4()),), role=ResearchRole.ATTRACTION, current_count=1, target_count=3)
    assert supplemental_gap_fingerprint(**common, gap=first) == supplemental_gap_fingerprint(**common, gap=same_target)
    assert supplemental_gap_fingerprint(**common, gap=first) != supplemental_gap_fingerprint(**common, gap=changed_target)


def test_avoid_gap_fingerprint_binds_group_items_and_ordered_candidate_target():
    common = dict(confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64)
    run_id = uuid4()
    ref = ArtifactRef(artifact_id=uuid4(), artifact_type="x.y", schema_version="1.0", content_hash="sha256:" + "c" * 64, operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="e"), created_at=datetime.now(timezone.utc), size_bytes=1)
    gap = AvoidResolutionMissingGap(
        gap_id="gap:a", evidence_refs=(ref,), requirement_group_id="group:a",
        requirement_item_ids=("item:0", "item:1"), candidate_uids=("u1", "u2"),
    )
    same = AvoidResolutionMissingGap.model_validate_json(gap.model_dump_json())
    assert supplemental_gap_fingerprint(**common, gap=gap) == supplemental_gap_fingerprint(**common, gap=same)
    for mutation in (
        {"requirement_group_id": "group:b"},
        {"requirement_item_ids": ("item:1", "item:0")},
        {"candidate_uids": ("u2", "u1")},
        {"candidate_uids": ("u1",)},
    ):
        changed = gap.model_copy(update=mutation)
        assert supplemental_gap_fingerprint(**common, gap=changed) != supplemental_gap_fingerprint(**common, gap=gap)


def test_request_digest_is_fresh_and_handles_are_bounded():
    request = _request(uuid4())
    assert invocation_request_digest(request) == invocation_request_digest(request)
    assert candidate_handle(0) == "c:0" and evidence_handle(47) == "e:47"
    with pytest.raises(ValueError):
        candidate_handle(128)
    with pytest.raises(ValueError):
        evidence_handle(48)
    with pytest.raises(ValueError):
        objective_handle("x" * 129)


def test_fingerprint_collision_detector_is_fail_closed_and_objective_stable():
    directory = {}
    register_fingerprint(directory, "sha256:" + "d" * 64, {"query": "museum"})
    register_fingerprint(directory, "sha256:" + "d" * 64, {"query": "museum"})
    with pytest.raises(ValueError):
        register_fingerprint(directory, "sha256:" + "d" * 64, {"query": "restaurant"})
    frozen = dict(
        confirmed_requirement_digest="sha256:" + "a" * 64,
        hotel_selection_content_hash="sha256:" + "b" * 64,
        predecessor_candidate_set_content_hash=None,
        gap_id=None, requirement_group_id=None, role=ResearchRole.ATTRACTION,
        interest=None, meal_intent=None, anchor=None, radius_km=None,
        candidate_uids=(), directory={},
    )
    assert build_objective_id(kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, **frozen) == build_objective_id(kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, **frozen)


def test_objective_requires_all_frozen_summaries_and_changes_for_each_summary():
    common = dict(kind=ResearchObjectiveKind.DIVERSITY, gap_id=None, requirement_group_id=None, role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=(), confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64, predecessor_candidate_set_content_hash=None, directory={})
    with pytest.raises(TypeError):
        build_objective_id(kind=ResearchObjectiveKind.DIVERSITY)
    base = build_objective_id(**common)
    assert build_objective_id(**{**common, "confirmed_requirement_digest": "sha256:" + "c" * 64}) != base
    assert build_objective_id(**{**common, "hotel_selection_content_hash": "sha256:" + "d" * 64}) != base
    assert build_objective_id(**{**common, "predecessor_candidate_set_content_hash": "sha256:" + "e" * 64}) != base
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "hotel_selection_content_hash": "bad"})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "candidate_uids": ("u1", "u1")})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "candidate_uids": tuple(f"u{i}" for i in range(129))})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "kind": ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, "role": None})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "role": ResearchRole.ATTRACTION})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "anchor": CandidateAnchorRef(uid="u1"), "radius_km": 3})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "kind": ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, "role": ResearchRole.RESTAURANT, "anchor": CandidateAnchorRef(uid="u1"), "radius_km": 3})
    with pytest.raises(ValueError):
        build_objective_id(**{**common, "candidate_uids": ["u1"]})


def test_requirement_and_objective_short_id_collisions_are_fail_closed(monkeypatch):
    class _Digest:
        def __init__(self, value):
            self.value = value

        def hexdigest(self):
            return self.value

    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "b" * 48))
    item_directory = {}
    requirement_item_id("sha256:" + "a" * 64, "must_visit", 0, "museum", directory=item_directory)
    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "c" * 48))
    with pytest.raises(ValueError):
        requirement_item_id("sha256:" + "a" * 64, "must_visit", 0, "restaurant", directory=item_directory)
    group_directory = {}
    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "b" * 48))
    requirement_group_id("sha256:" + "a" * 64, "must_visit", ("reqitem:must_visit:0:x",), 0, "museum", directory=group_directory)
    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "c" * 48))
    with pytest.raises(ValueError):
        requirement_group_id("sha256:" + "a" * 64, "must_visit", ("reqitem:must_visit:0:y",), 0, "restaurant", directory=group_directory)
    objective_directory = {}
    objective_kwargs = dict(
        kind=ResearchObjectiveKind.DIVERSITY, gap_id=None, requirement_group_id=None,
        role=None, interest=None, meal_intent=None, anchor=None, radius_km=None,
        candidate_uids=(), confirmed_requirement_digest="sha256:" + "a" * 64,
        hotel_selection_content_hash="sha256:" + "b" * 64,
        predecessor_candidate_set_content_hash=None, directory=objective_directory,
    )
    build_objective_id(**objective_kwargs)
    with pytest.raises(ValueError):
        monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "d" * 48))
        build_objective_id(**{**objective_kwargs, "predecessor_candidate_set_content_hash": "sha256:" + "e" * 64})


def test_objective_directories_are_run_private_and_do_not_leak_state():
    kwargs = dict(
        kind=ResearchObjectiveKind.DIVERSITY, gap_id=None, requirement_group_id=None,
        role=None, interest=None, meal_intent=None, anchor=None, radius_km=None,
        candidate_uids=(), confirmed_requirement_digest="sha256:" + "a" * 64,
        hotel_selection_content_hash="sha256:" + "b" * 64,
        predecessor_candidate_set_content_hash=None,
    )
    first_directory, second_directory = {}, {}
    first = build_objective_id(**kwargs, directory=first_directory)
    second = build_objective_id(**kwargs, directory=second_directory)
    assert first == second
    assert first_directory == second_directory


def test_short_id_directory_rejects_same_prefix_with_different_full_digest(monkeypatch):
    class _Digest:
        def __init__(self, value):
            self.value = value

        def hexdigest(self):
            return self.value

    directory = {}
    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "b" * 48))
    fingerprint_module._short_id("objective:test", {"x": 1}, directory=directory)
    monkeypatch.setattr(fingerprint_module.hashlib, "sha256", lambda _: _Digest("a" * 16 + "c" * 48))
    with pytest.raises(ValueError):
        fingerprint_module._short_id("objective:test", {"x": 2}, directory=directory)
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


@pytest.mark.parametrize("query,tag", [(" ", None), ("museum", " "), (1, None), ("museum", 1)])
def test_search_fingerprint_rejects_empty_or_non_string_query_fields(query, tag):
    with pytest.raises(ValueError):
        search_action_fingerprint(query=query, tag=tag, area_mode=SearchAreaMode.CITY, destination="杭州")


@pytest.mark.parametrize("kwargs", [
    dict(area_mode=SearchAreaMode.CITY, destination=None, anchor=None, radius_km=None),
    dict(area_mode=SearchAreaMode.CITY, destination="杭州", anchor=CandidateAnchorRef(uid="u1"), radius_km=None),
    dict(area_mode=SearchAreaMode.NEARBY, destination=None, anchor=None, radius_km=3),
    dict(area_mode=SearchAreaMode.NEARBY, destination=None, anchor=CandidateAnchorRef(uid="u1"), radius_km=2),
])
def test_search_fingerprint_covers_city_and_nearby_pair_guards(kwargs):
    with pytest.raises(ValueError):
        search_action_fingerprint(query="museum", tag=None, **kwargs)


@pytest.mark.parametrize("uid", ("", " ", "u" * 513, 1))
def test_details_and_handle_bounds_are_strict(uid):
    with pytest.raises(ValueError):
        details_action_fingerprint(uid)
    if isinstance(uid, int):
        with pytest.raises(ValueError):
            objective_handle(uid)


@pytest.mark.parametrize("field,index,text", [("x", 0, "museum"), ("must_visit", -1, "museum"), ("must_visit", 100, "museum"), ("must_visit", 0, " "), ("must_visit", 0, "x" * 513)])
def test_requirement_item_validation_covers_each_input_guard(field, index, text):
    with pytest.raises(ValueError):
        requirement_item_id("sha256:" + "a" * 64, field, index, text, directory={})


def test_requirement_group_validation_covers_tuple_uniqueness_and_directory_guards():
    valid = dict(confirmed_requirement_digest="sha256:" + "a" * 64, field="must_visit", item_ids=("i1",), first_index=0, text="museum")
    assert requirement_group_id(**valid, directory={}).startswith("reqgroup:")
    for mutation in (
        {"item_ids": ()}, {"item_ids": ("i1", "i1")}, {"first_index": 100}, {"field": "other"},
    ):
        changed = dict(valid); changed.update(mutation)
        with pytest.raises(ValueError):
            requirement_group_id(**changed, directory={})
    with pytest.raises(ValueError):
        requirement_group_id(**valid, directory=[])


def test_register_fingerprint_validates_digest_and_normalizes_equivalent_trees():
    directory = {}
    with pytest.raises(ValueError):
        register_fingerprint(directory, "bad", {"a": 1})
    register_fingerprint(directory, "sha256:" + "a" * 64, {"a": 1})
    register_fingerprint(directory, "sha256:" + "a" * 64, {"a": 1})
    with pytest.raises(ValueError):
        register_fingerprint(directory, "sha256:" + "a" * 64, {"a": 2})


@pytest.mark.parametrize("kind,kwargs", [
    (ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, {"role": ResearchRole.RESTAURANT, "anchor": HotelAnchorRef(), "radius_km": 3}),
    (ResearchObjectiveKind.INTEREST_COVERAGE, {"interest": InterestTag.CULTURE}),
])
def test_objective_fingerprint_supports_optional_typed_fields(kind, kwargs):
    base = dict(kind=kind, gap_id=None, requirement_group_id=None, role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=(), confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64, predecessor_candidate_set_content_hash=None, directory={})
    base.update(kwargs)
    assert objective_fingerprint(**base).startswith("objective:")


@pytest.mark.parametrize("name,value", [("confirmed_requirement_digest", "bad"), ("hotel_selection_content_hash", "bad"), ("predecessor_candidate_set_content_hash", "bad"), ("candidate_uids", ["u1"])])
def test_objective_fingerprint_rejects_noncanonical_inputs(name, value):
    base = dict(kind=ResearchObjectiveKind.DIVERSITY, gap_id=None, requirement_group_id=None, role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=(), confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_content_hash="sha256:" + "b" * 64, predecessor_candidate_set_content_hash=None, directory={})
    base[name] = value
    with pytest.raises(ValueError):
        objective_fingerprint(**base)
