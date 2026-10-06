"""DLG-T04: presence-aware merge, derivation, issues and metadata tests."""

from uuid import uuid4

import pytest

from app.requirements.merge import RequirementMerger, update_metadata_for_patch
from app.requirements.models import (
    EvidenceReceipt,
    FieldMetadataRegistry,
    FieldStatus,
    InterestTag,
    NormalizationRecord,
    RequirementEvidence,
    RequirementIssue,
    RequirementFieldPath,
    RequirementPatch,
    TravelBudget,
    TravelConstraints,
    TravelParty,
    TravelPreferences,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
)


def test_empty_patch_is_a_complete_no_op():
    merger = RequirementMerger()
    current = TravelRequirement(destination="杭州")
    before = current.model_copy(deep=True)
    result = merger.merge(current, RequirementPatch())
    assert result.requirement == before
    assert result.requirement is not current
    assert result.issues == ()
    assert result.changed_fields == ()
    assert result.derived_fields == ()
    assert current == before


def test_correction_overwrites_and_non_correction_conflicts():
    merger = RequirementMerger()
    current = TravelRequirement(destination="上海", budget=TravelBudget(total_cny=1000.0))

    conflict = merger.merge(current, RequirementPatch(destination="北京", budget=TravelBudget(total_cny=2000.0)))
    assert conflict.requirement.destination == "上海"
    assert conflict.requirement.budget.total_cny == 1000.0
    assert {issue.field_path for issue in conflict.issues} == {"destination", "budget.total_cny"}
    assert all(issue.code == "requirement_conflict" for issue in conflict.issues)

    corrected = merger.merge(
        current,
        RequirementPatch(destination="北京", budget=TravelBudget(total_cny=2000.0), is_correction=True),
    )
    assert corrected.requirement.destination == "北京"
    assert corrected.requirement.budget.total_cny == 2000.0
    assert corrected.issues == ()


def test_nested_presence_does_not_reset_unprovided_values():
    merger = RequirementMerger()
    current = TravelRequirement(
        party=TravelParty(adults_count=1, children_count=1, children_ages=[8], seniors_count=0),
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(is_approximate=True),
            departure=TravelTimeWindowDeparture(is_approximate=True),
        ),
    )
    patch = RequirementPatch(
        party=TravelParty(adults_count=2),
        time_window=TravelTimeWindow(sightseeing_start_date="2025-06-01"),
        is_correction=True,
    )
    result = merger.merge(current, patch).requirement
    assert result.party.children_ages == [8]
    assert result.time_window.arrival.is_approximate is True
    assert result.time_window.departure.is_approximate is True


def test_explicit_empty_list_is_not_treated_as_absent():
    merger = RequirementMerger()
    current = TravelRequirement(party=TravelParty(adults_count=2, children_count=1, children_ages=[8], seniors_count=0))
    patch = RequirementPatch(
        party=TravelParty(children_count=0, children_ages=[]),
        is_correction=True,
    )
    result = merger.merge(current, patch)
    assert result.requirement.party.children_count == 0
    assert result.requirement.party.children_ages == []


def test_total_people_and_scenarios_are_recomputed():
    merger = RequirementMerger()
    patch = RequirementPatch(
        party=TravelParty(adults_count=2, children_count=1, children_ages=[5], seniors_count=1),
        constraints=TravelConstraints(mobility_constraints="需要轮椅"),
    )
    result = merger.merge(TravelRequirement(), patch)
    assert result.requirement.party.total_people == 4
    assert result.requirement.inferred_scenarios == [
        "family_with_children",
        "travel_with_seniors",
        "mobility_assistance_needed",
    ]
    assert "party.total_people" in result.derived_fields
    assert "inferred_scenarios" in result.derived_fields


@pytest.mark.parametrize("text", (
    "无", "没有", "none", "None",
    "没有行动限制", "无行动限制", "没有行动限制和饮食禁忌", "无行动限制和饮食禁忌",
    "行动限制：无", "行动限制: 无", "行动限制 ： 没有行动限制",
    "行动限制：没有行动限制和饮食禁忌", " 行动限制 ： 无 ",
))
def test_explicit_no_mobility_constraint_removes_only_mobility_scenario(text):
    current = TravelRequirement(
        party=TravelParty(children_count=1, seniors_count=1),
        constraints=TravelConstraints(mobility_constraints="需要轮椅"),
        inferred_scenarios=["family_with_children", "travel_with_seniors", "mobility_assistance_needed"],
    )
    result = RequirementMerger().merge(current, RequirementPatch(
        constraints=TravelConstraints(mobility_constraints=text), is_correction=True))
    assert result.requirement.constraints.mobility_constraints == text
    assert result.requirement.inferred_scenarios == ["family_with_children", "travel_with_seniors"]
    assert "mobility_assistance_needed" in current.inferred_scenarios
    assert current.constraints.mobility_constraints == "需要轮椅"


@pytest.mark.parametrize("text", ("行动限制：无", "行动限制: 无"))
def test_no_mobility_supplement_requires_explicit_correction(text):
    current = TravelRequirement(
        constraints=TravelConstraints(mobility_constraints="需要轮椅"),
        inferred_scenarios=["mobility_assistance_needed"],
    )
    before = current.model_copy(deep=True)
    result = RequirementMerger().merge(current, RequirementPatch(
        constraints=TravelConstraints(mobility_constraints=text)))
    assert result.requirement == before
    assert [(issue.code, issue.field_path) for issue in result.issues] == [
        ("requirement_conflict", RequirementFieldPath.MOBILITY_CONSTRAINTS)]
    assert result.changed_fields == ()
    assert current == before


@pytest.mark.parametrize("text", (
    "行动不便", "没有轮椅，不能久走", "没有行动限制，但同行者需要轮椅",
    "行动限制：无，但不能久走", "行动限制: 没有行动限制，但同行者需要轮椅",
    "行动限制：", "饮食禁忌：无",
    "行动限制：行动限制：无", "行动限制::无",
))
def test_negation_does_not_erase_a_positive_mobility_constraint(text):
    result = RequirementMerger().merge(TravelRequirement(), RequirementPatch(
        constraints=TravelConstraints(mobility_constraints=text)))
    assert result.requirement.inferred_scenarios == ["mobility_assistance_needed"]


def test_dates_and_hotel_fields_are_recomputed_from_dependencies():
    merger = RequirementMerger()
    patch = RequirementPatch(
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(local_datetime="2025-06-01 09:00"),
            departure=TravelTimeWindowDeparture(local_datetime="2025-06-04 18:00"),
            sightseeing_start_date="2025-06-01",
            sightseeing_end_date="2025-06-03",
        )
    )
    result = merger.merge(TravelRequirement(), patch)
    tw = result.requirement.time_window
    assert tw.sightseeing_days == 3
    assert tw.hotel_check_in_date == "2025-06-01"
    assert tw.hotel_check_out_date == "2025-06-04"
    assert tw.hotel_nights == 3


def test_date_dependency_change_recomputes_old_derived_days():
    merger = RequirementMerger()
    current = TravelRequirement(
        time_window=TravelTimeWindow(
            sightseeing_start_date="2025-06-01",
            sightseeing_end_date="2025-06-03",
            sightseeing_days=3,
        )
    )
    result = merger.merge(
        current,
        RequirementPatch(
            time_window=TravelTimeWindow(sightseeing_end_date="2025-06-04"),
            is_correction=True,
        ),
    )
    assert result.requirement.time_window.sightseeing_days == 4
    assert "time_window.sightseeing_days" in result.derived_fields


def test_explicit_days_are_validated_instead_of_overwritten():
    merger = RequirementMerger()
    result = merger.merge(
        TravelRequirement(),
        RequirementPatch(
            time_window=TravelTimeWindow(
                sightseeing_start_date="2025-06-01",
                sightseeing_end_date="2025-06-03",
                sightseeing_days=10,
            )
        ),
    )
    assert result.requirement.time_window.sightseeing_days == 10
    assert any(issue.code == "days_mismatch" for issue in merger.detect_conflicts(result.requirement))


def test_detect_missing_and_all_cross_field_conflicts():
    merger = RequirementMerger()
    assert "destination" in merger.detect_missing_fields(TravelRequirement())

    req = TravelRequirement(
        party=TravelParty(adults_count=2, children_count=0, seniors_count=0),
        rooms=TravelRooms(room_count=1, room_type_requirements="标准间"),
    )
    req.rooms.room_count = 0
    issues = merger.detect_conflicts(req)
    assert any(issue.field_path == "rooms.room_count" for issue in issues)


def test_downstream_invalidation_includes_preferences():
    merger = RequirementMerger()
    before = TravelRequirement(preferences=TravelPreferences(interests=[InterestTag.NATURE]))
    after = TravelRequirement(preferences=TravelPreferences(interests=[InterestTag.FOOD]))
    assert merger.detect_downstream_invalidation(before, after) == ["preferences"]


def test_metadata_uses_receipts_normalization_issues_and_derived_status():
    registry = FieldMetadataRegistry()
    patch = RequirementPatch(destination="杭州")
    turn_id = uuid4()
    patch.evidence = [
        RequirementEvidence(
            field_path=RequirementFieldPath.DESTINATION,
            text="杭州",
            turn_id=turn_id,
        )
    ]
    normalization = NormalizationRecord(raw_value="明天", normalized_value="2025-06-02", rule_name="relative_date")
    issue = RequirementIssue(
        code="requirement_conflict",
        field_path=RequirementFieldPath.BUDGET_TOTAL_CNY,
        message="conflict",
    )

    update_metadata_for_patch(
        registry,
        patch,
        turn_id,
        receipts=(EvidenceReceipt(field_path=RequirementFieldPath.DESTINATION, evidence_ref="ref-1", evidence_digest="digest-1"),),
        normalizations={"time_window.sightseeing_start_date": normalization},
        issues=(issue,),
        derived_fields=("party.total_people",),
        pending_confirmation_fields=("time_window.sightseeing_start_date",),
    )

    destination = registry.metadata["destination"]
    assert destination.evidence_ref == "ref-1"
    assert destination.evidence_digest == "digest-1"
    assert destination.status == FieldStatus.EXTRACTED
    assert registry.metadata["time_window.sightseeing_start_date"].status == FieldStatus.PENDING_CONFIRMATION
    assert registry.metadata["party.total_people"].status == FieldStatus.DERIVED
    assert registry.metadata["budget.total_cny"].status == FieldStatus.CONFLICT


def test_clear_requires_correction_and_current_evidence():
    merger = RequirementMerger()
    current = TravelRequirement(
        constraints=TravelConstraints(dietary_restrictions="不吃辣"),
        rooms=TravelRooms(room_count=1, room_type_requirements="标准间"),
    )
    without_evidence = merger.merge(
        current,
        RequirementPatch(
            constraints=TravelConstraints(dietary_restrictions=None),
            rooms=TravelRooms(room_type_requirements=None),
            is_correction=True,
        ),
    )
    assert {issue.code for issue in without_evidence.issues} == {"invalid_clear"}
    assert without_evidence.requirement.constraints.dietary_restrictions == "不吃辣"

    patch = RequirementPatch(
        constraints=TravelConstraints(dietary_restrictions=None),
        is_correction=True,
        evidence=[
            RequirementEvidence(
                field_path=RequirementFieldPath.DIETARY_RESTRICTIONS,
                text="没有禁忌",
            )
        ],
    )
    cleared = merger.merge(current, patch)
    assert cleared.requirement.constraints.dietary_restrictions is None
    assert cleared.issues == ()


def test_all_nested_patch_groups_use_leaf_presence():
    merger = RequirementMerger()
    patch = RequirementPatch(
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="萧山机场",
                local_datetime="2025-06-01 08:00",
                is_approximate=True,
            ),
            departure=TravelTimeWindowDeparture(
                hub_name="杭州东站",
                local_datetime="2025-06-03 18:00",
                is_approximate=True,
            ),
        ),
        budget=TravelBudget(total_cny=5000.0, is_approximate=True),
        rooms=TravelRooms(room_count=1, room_type_requirements="大床房"),
        constraints=TravelConstraints(mobility_constraints="无", dietary_restrictions="无"),
        preferences=TravelPreferences(
            interests=[InterestTag.NATURE],
            agent_decides_pace=True,
            transport_preferences="地铁",
            hotel_preferences="安静",
            must_visit_places=["西湖"],
            avoid_places=["商场"],
            daily_time_preferences="早出晚归",
            free_text_notes="无",
        ),
    )
    result = merger.merge(TravelRequirement(), patch)
    req = result.requirement
    assert req.time_window.arrival.hub_name == "萧山机场"
    assert req.time_window.departure.hub_name == "杭州东站"
    assert req.budget.is_approximate is True
    assert req.rooms.room_type_requirements == "大床房"
    assert req.constraints.dietary_restrictions == "无"
    assert req.preferences.agent_decides_pace is True
    assert req.preferences.must_visit_places == ["西湖"]
    assert req.preferences.free_text_notes == "无"


def test_same_value_is_noop_and_merge_handles_bad_dependencies():
    merger = RequirementMerger()
    current = TravelRequirement(destination="杭州")
    same = merger.merge(current, RequirementPatch(destination="杭州"))
    assert same.changed_fields == ()
    assert same.issues == ()

    current.time_window.sightseeing_start_date = "bad"
    current.time_window.sightseeing_end_date = "2025-06-02"
    current.time_window.sightseeing_days = 3
    current.time_window.arrival.local_datetime = "bad"
    current.time_window.departure.local_datetime = "2025-06-02 10:00"
    current.time_window.hotel_check_in_date = "2025-01-01"
    current.time_window.hotel_nights = 2
    result = merger.merge(
        current,
        RequirementPatch(
            time_window=TravelTimeWindow(
                sightseeing_end_date="2025-06-03",
                departure=TravelTimeWindowDeparture(local_datetime="2025-06-03 10:00"),
            ),
            is_correction=True,
        ),
    )
    assert result.requirement.time_window.sightseeing_days is None
    assert result.requirement.time_window.hotel_check_in_date is None
    assert result.requirement.time_window.hotel_nights is None
    assert "time_window.sightseeing_days" in result.derived_fields


def test_missing_children_age_and_no_invalidation_are_explicit():
    merger = RequirementMerger()
    req = TravelRequirement(party=TravelParty(children_count=1, children_ages=[]))
    assert "party.children_ages" in merger.detect_missing_fields(req)
    assert merger.detect_downstream_invalidation(req, req.model_copy(deep=True)) is None


def test_metadata_can_mark_invalid_and_enumerate_time_leaf_paths():
    registry = FieldMetadataRegistry()
    patch = RequirementPatch(
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(hub_name="杭州东站"),
            departure=TravelTimeWindowDeparture(local_datetime="2025-06-03 18:00"),
            sightseeing_start_date="2025-06-01",
        )
    )
    issue = RequirementIssue(
        code="value_out_of_range",
        field_path=RequirementFieldPath.ROOM_COUNT,
        message="invalid",
    )
    update_metadata_for_patch(registry, patch, uuid4(), issues=(issue,))
    assert registry.metadata["time_window.arrival.hub_name"].status == FieldStatus.EXTRACTED
    assert registry.metadata["time_window.departure.local_datetime"].status == FieldStatus.EXTRACTED
    assert registry.metadata["time_window.sightseeing_start_date"].status == FieldStatus.EXTRACTED
    assert registry.metadata["rooms.room_count"].status == FieldStatus.INVALID
