from __future__ import annotations

from dataclasses import dataclass
from .interest_policy import interest_selection_error, pace_selection_error
from datetime import date, datetime
from typing import Callable, Iterable, Mapping, Optional, Sequence
from uuid import UUID

from app.requirements.models import (
    EvidenceReceipt,
    FieldMetadata,
    FieldMetadataRegistry,
    FieldStatus,
    NormalizationRecord,
    RequirementFieldPath,
    RequirementIssue,
    RequirementPatch,
    TravelRequirement,
)
from app.requirements.normalization import (
    infer_hub_type,
    validate_party,
    validate_requirement_bounds,
    validate_requirement_hubs,
    validate_rooms,
    validate_time_window,
)
from app.requirements.collection_policy import deferred_field_paths


@dataclass(frozen=True)
class MergeResult:
    requirement: TravelRequirement
    issues: tuple[RequirementIssue, ...]
    changed_fields: tuple[str, ...]
    derived_fields: tuple[str, ...]


def _fields_set(model: object) -> set[str]:
    return set(getattr(model, "model_fields_set", set()))


def _is_unset_domain_value(value: object) -> bool:
    return value is None or value is False or value == []


def _conflict(path: str) -> RequirementIssue:
    return RequirementIssue(
        code="requirement_conflict",
        field_path=RequirementFieldPath(path),
        message=f"{path} 的新值与已保存值不一致，请明确这是纠正",
    )


class RequirementMerger:
    def merge(self, current: TravelRequirement, patch: RequirementPatch, *,
              verified_paths: Iterable[str] = ()) -> MergeResult:
        updated = current.model_copy(deep=True)
        patch_fields = _fields_set(patch) - {"evidence", "is_correction"}
        if not patch_fields:
            return MergeResult(updated, (), (), ())

        issues: list[RequirementIssue] = []
        changed: list[str] = []
        evidence_paths = {item.field_path.value for item in patch.evidence} | set(verified_paths)
        invalid_hub_fields = {
            issue.field_path.value
            for issue in validate_requirement_hubs(current)
            if issue.code in {"hub_name_not_specific", "hub_type_mismatch"}
        }
        invalid_interests = interest_selection_error(current.preferences) is not None
        invalid_pace = pace_selection_error(current.preferences) is not None

        def evidenced_interest_repair(path: str) -> bool:
            return ((invalid_interests and path in {
                "preferences.interests", "preferences.agent_decides_interests"
            } or invalid_pace and path in {"preferences.pace", "preferences.agent_decides_pace"}) and path in evidence_paths)

        def evidenced_hub_repair(path: str, new: object) -> bool:
            prefix, _, field = path.rpartition(".")
            if path not in invalid_hub_fields or field not in {"hub_name", "hub_type"}:
                return False
            if path in evidence_paths:
                return True
            # M01 may derive the type from the verified name in this same patch.
            if field == "hub_type" and f"{prefix}.hub_name" in evidence_paths:
                hub = getattr(patch.time_window, prefix.rsplit(".", 1)[1])
                return bool(hub.hub_name and infer_hub_type(hub.hub_name) == new)
            return False

        def apply(
            path: str,
            present: bool,
            old: object,
            new: object,
            setter: Callable[[object], None],
            *,
            clearable: bool = False,
        ) -> None:
            if not present:
                return
            if new is None:
                if old is None:
                    return
                if clearable and patch.is_correction and path in evidence_paths:
                    setter(None)
                    changed.append(path)
                else:
                    issues.append(RequirementIssue(
                        code="invalid_clear",
                        field_path=RequirementFieldPath(path),
                        message=f"{path} 不能在没有当前轮纠正证据时清空",
                    ))
                return
            if old == new:
                return
            if _is_unset_domain_value(old) or patch.is_correction or evidenced_hub_repair(path, new) or evidenced_interest_repair(path):
                setter(new)
                changed.append(path)
            else:
                issues.append(_conflict(path))

        if "destination" in patch_fields:
            apply("destination", True, updated.destination, patch.destination, lambda v: setattr(updated, "destination", v))

        if "time_window" in patch_fields and patch.time_window is not None:
            source = patch.time_window
            source_fields = _fields_set(source)
            if "arrival" in source_fields:
                arrival_fields = _fields_set(source.arrival)
                apply("time_window.arrival.hub_name", "hub_name" in arrival_fields, updated.time_window.arrival.hub_name, source.arrival.hub_name, lambda v: setattr(updated.time_window.arrival, "hub_name", v))
                apply("time_window.arrival.hub_type", "hub_type" in arrival_fields, updated.time_window.arrival.hub_type, source.arrival.hub_type, lambda v: setattr(updated.time_window.arrival, "hub_type", v))
                apply("time_window.arrival.local_datetime", "local_datetime" in arrival_fields, updated.time_window.arrival.local_datetime, source.arrival.local_datetime, lambda v: setattr(updated.time_window.arrival, "local_datetime", v))
                apply("time_window.arrival.is_approximate", "is_approximate" in arrival_fields, updated.time_window.arrival.is_approximate, source.arrival.is_approximate, lambda v: setattr(updated.time_window.arrival, "is_approximate", v))
            if "departure" in source_fields:
                departure_fields = _fields_set(source.departure)
                apply("time_window.departure.hub_name", "hub_name" in departure_fields, updated.time_window.departure.hub_name, source.departure.hub_name, lambda v: setattr(updated.time_window.departure, "hub_name", v))
                apply("time_window.departure.hub_type", "hub_type" in departure_fields, updated.time_window.departure.hub_type, source.departure.hub_type, lambda v: setattr(updated.time_window.departure, "hub_type", v))
                apply("time_window.departure.local_datetime", "local_datetime" in departure_fields, updated.time_window.departure.local_datetime, source.departure.local_datetime, lambda v: setattr(updated.time_window.departure, "local_datetime", v))
                apply("time_window.departure.is_approximate", "is_approximate" in departure_fields, updated.time_window.departure.is_approximate, source.departure.is_approximate, lambda v: setattr(updated.time_window.departure, "is_approximate", v))
            apply("time_window.sightseeing_start_date", "sightseeing_start_date" in source_fields, updated.time_window.sightseeing_start_date, source.sightseeing_start_date, lambda v: setattr(updated.time_window, "sightseeing_start_date", v))
            apply("time_window.sightseeing_end_date", "sightseeing_end_date" in source_fields, updated.time_window.sightseeing_end_date, source.sightseeing_end_date, lambda v: setattr(updated.time_window, "sightseeing_end_date", v))
            apply("time_window.sightseeing_days", "sightseeing_days" in source_fields, updated.time_window.sightseeing_days, source.sightseeing_days, lambda v: setattr(updated.time_window, "sightseeing_days", v))

        if "budget" in patch_fields and patch.budget is not None:
            fields = _fields_set(patch.budget)
            apply("budget.total_cny", "total_cny" in fields, updated.budget.total_cny, patch.budget.total_cny, lambda v: setattr(updated.budget, "total_cny", v))
            apply("budget.is_approximate", "is_approximate" in fields, updated.budget.is_approximate, patch.budget.is_approximate, lambda v: setattr(updated.budget, "is_approximate", v))

        if "party" in patch_fields and patch.party is not None:
            fields = _fields_set(patch.party)
            apply("party.adults_count", "adults_count" in fields, updated.party.adults_count, patch.party.adults_count, lambda v: setattr(updated.party, "adults_count", v))
            apply("party.children_count", "children_count" in fields, updated.party.children_count, patch.party.children_count, lambda v: setattr(updated.party, "children_count", v))
            apply("party.children_ages", "children_ages" in fields, updated.party.children_ages, patch.party.children_ages, lambda v: setattr(updated.party, "children_ages", v))
            apply("party.seniors_count", "seniors_count" in fields, updated.party.seniors_count, patch.party.seniors_count, lambda v: setattr(updated.party, "seniors_count", v))

        if "rooms" in patch_fields and patch.rooms is not None:
            fields = _fields_set(patch.rooms)
            apply("rooms.room_count", "room_count" in fields, updated.rooms.room_count, patch.rooms.room_count, lambda v: setattr(updated.rooms, "room_count", v))
            apply("rooms.room_type_requirements", "room_type_requirements" in fields, updated.rooms.room_type_requirements, patch.rooms.room_type_requirements, lambda v: setattr(updated.rooms, "room_type_requirements", v), clearable=True)

        if "constraints" in patch_fields and patch.constraints is not None:
            fields = _fields_set(patch.constraints)
            apply("constraints.mobility_constraints", "mobility_constraints" in fields, updated.constraints.mobility_constraints, patch.constraints.mobility_constraints, lambda v: setattr(updated.constraints, "mobility_constraints", v), clearable=True)
            apply("constraints.dietary_restrictions", "dietary_restrictions" in fields, updated.constraints.dietary_restrictions, patch.constraints.dietary_restrictions, lambda v: setattr(updated.constraints, "dietary_restrictions", v), clearable=True)

        if "preferences" in patch_fields and patch.preferences is not None:
            fields = _fields_set(patch.preferences)
            preference_specs = (
                ("interests", True),
                ("pace", True),
                ("agent_decides_interests", False),
                ("agent_decides_pace", False),
                ("transport_preferences", True),
                ("hotel_preferences", True),
                ("must_visit_places", False),
                ("avoid_places", False),
                ("daily_time_preferences", True),
                ("free_text_notes", True),
            )
            for attribute, clearable in preference_specs:
                apply(
                    f"preferences.{attribute}",
                    attribute in fields,
                    getattr(updated.preferences, attribute),
                    getattr(patch.preferences, attribute),
                    lambda value, name=attribute: setattr(updated.preferences, name, value),
                    clearable=clearable,
                )
            # The UI asks one mutually exclusive choice to repair an invalid
            # interest group. Only current-turn verified evidence authorizes it.
            choose_agent = ("agent_decides_interests" in fields
                and patch.preferences.agent_decides_interests
                and evidenced_interest_repair("preferences.agent_decides_interests"))
            choose_values = ("interests" in fields and bool(patch.preferences.interests)
                and evidenced_interest_repair("preferences.interests"))
            if choose_agent and not choose_values:
                updated.preferences.interests = None
                changed.append("preferences.interests")
            elif choose_values and not choose_agent:
                updated.preferences.agent_decides_interests = False
                changed.append("preferences.agent_decides_interests")
            choose_pace_agent = ("agent_decides_pace" in fields and patch.preferences.agent_decides_pace
                and evidenced_interest_repair("preferences.agent_decides_pace"))
            choose_pace = ("pace" in fields and patch.preferences.pace is not None
                and evidenced_interest_repair("preferences.pace"))
            if choose_pace_agent and not choose_pace:
                updated.preferences.pace = None
                changed.append("preferences.pace")
            elif choose_pace and not choose_pace_agent:
                updated.preferences.agent_decides_pace = False
                changed.append("preferences.agent_decides_pace")

        derived = self._recompute_derived(updated, set(changed), patch)
        return MergeResult(updated, tuple(issues), tuple(changed), derived)

    def _recompute_derived(
        self,
        req: TravelRequirement,
        changed_fields: set[str],
        patch: RequirementPatch,
    ) -> tuple[str, ...]:
        derived: list[str] = []

        party_dependencies = {"party.adults_count", "party.children_count", "party.seniors_count"}
        if changed_fields & party_dependencies:
            counts = (req.party.adults_count, req.party.children_count, req.party.seniors_count)
            new_total = None if all(value is None for value in counts) else sum(value or 0 for value in counts)
            if req.party.total_people != new_total:
                req.party.total_people = new_total
                derived.append("party.total_people")

        explicit_days = False
        if patch.time_window is not None:
            explicit_days = "sightseeing_days" in _fields_set(patch.time_window)
        date_dependencies = {"time_window.sightseeing_start_date", "time_window.sightseeing_end_date"}
        if changed_fields & date_dependencies and not explicit_days:
            computed_days: Optional[int] = None
            try:
                if req.time_window.sightseeing_start_date and req.time_window.sightseeing_end_date:
                    start = date.fromisoformat(req.time_window.sightseeing_start_date)
                    end = date.fromisoformat(req.time_window.sightseeing_end_date)
                    if end >= start:
                        computed_days = (end - start).days + 1
            except ValueError:
                computed_days = None
            if req.time_window.sightseeing_days != computed_days:
                req.time_window.sightseeing_days = computed_days
                derived.append("time_window.sightseeing_days")

        hotel_dependency_paths = {
            "time_window.arrival.local_datetime",
            "time_window.departure.local_datetime",
        }
        if changed_fields & hotel_dependency_paths:
            check_in = self._date_from_local_datetime(req.time_window.arrival.local_datetime)
            check_out = self._date_from_local_datetime(req.time_window.departure.local_datetime)
            check_in_text = check_in.isoformat() if check_in else None
            check_out_text = check_out.isoformat() if check_out else None
            if req.time_window.hotel_check_in_date != check_in_text:
                req.time_window.hotel_check_in_date = check_in_text
                derived.append("time_window.hotel_check_in_date")
            if req.time_window.hotel_check_out_date != check_out_text:
                req.time_window.hotel_check_out_date = check_out_text
                derived.append("time_window.hotel_check_out_date")
            nights = (check_out - check_in).days if check_in and check_out and check_out >= check_in else None
            if req.time_window.hotel_nights != nights:
                req.time_window.hotel_nights = nights
                derived.append("time_window.hotel_nights")

        scenario_dependencies = party_dependencies | {"constraints.mobility_constraints"}
        if changed_fields & scenario_dependencies:
            scenarios: list[str] = []
            if (req.party.children_count or 0) > 0:
                scenarios.append("family_with_children")
            if (req.party.seniors_count or 0) > 0:
                scenarios.append("travel_with_seniors")
            mobility = (req.constraints.mobility_constraints or "").strip()
            # A form label may be part of the exact extracted evidence. Compare
            # its answer without changing the persisted constraint wording.
            mobility_answer = mobility
            label, separator, answer = mobility.replace("：", ":", 1).partition(":")
            if separator and label.strip() == "行动限制":
                mobility_answer = answer.strip()
            if mobility and mobility_answer not in {
                "无", "没有", "none", "None", "无行动限制", "没有行动限制",
                "无行动限制和饮食禁忌", "没有行动限制和饮食禁忌",
            }:
                scenarios.append("mobility_assistance_needed")
            if req.inferred_scenarios != scenarios:
                req.inferred_scenarios = scenarios
                derived.append("inferred_scenarios")
        return tuple(derived)

    @staticmethod
    def _date_from_local_datetime(value: Optional[str]) -> Optional[date]:
        if value is None:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M").date()
        except ValueError:
            return None

    def detect_missing_fields(self, req: TravelRequirement, *, collection_version="1.0") -> list[str]:
        missing: list[str] = []
        required_values = (
            ("destination", req.destination),
            ("time_window.sightseeing_start_date", req.time_window.sightseeing_start_date),
            ("time_window.sightseeing_end_date", req.time_window.sightseeing_end_date),
            ("time_window.sightseeing_days", req.time_window.sightseeing_days),
            ("time_window.arrival.hub_name", req.time_window.arrival.hub_name),
            ("time_window.arrival.hub_type", req.time_window.arrival.hub_type),
            ("time_window.arrival.local_datetime", req.time_window.arrival.local_datetime),
            ("time_window.departure.hub_name", req.time_window.departure.hub_name),
            ("time_window.departure.hub_type", req.time_window.departure.hub_type),
            ("time_window.departure.local_datetime", req.time_window.departure.local_datetime),
            ("party.adults_count", req.party.adults_count),
            ("party.children_count", req.party.children_count),
            ("party.seniors_count", req.party.seniors_count),
            ("rooms.room_count", req.rooms.room_count),
            ("rooms.room_type_requirements", req.rooms.room_type_requirements),
            ("constraints.mobility_constraints", req.constraints.mobility_constraints),
            ("constraints.dietary_restrictions", req.constraints.dietary_restrictions),
        )
        for path, value in required_values:
            if path in deferred_field_paths(collection_version):
                continue
            if value is None or isinstance(value, str) and not value.strip():
                missing.append(path)
        if collection_version == "1.0" and (req.party.children_count or 0) > 0 and not req.party.children_ages:
            missing.append("party.children_ages")
        return missing

    def detect_conflicts(self, req: TravelRequirement, *, collection_version="1.0") -> list[RequirementIssue]:
        issues = [
            *validate_requirement_bounds(req),
            *validate_requirement_hubs(req),
            *validate_time_window(req.time_window),
            *validate_party(req.party.adults_count, req.party.children_count, req.party.children_ages, req.party.seniors_count),
            *validate_rooms(req.rooms.room_count, req.party.total_people or 0),
        ]
        return [issue for issue in issues if issue.field_path.value not in deferred_field_paths(collection_version)]

    def detect_downstream_invalidation(
        self, before: TravelRequirement, after: TravelRequirement
    ) -> Optional[list[str]]:
        modified: list[str] = []
        for field in ("destination", "time_window", "budget", "party", "rooms", "constraints", "preferences"):
            if getattr(before, field) != getattr(after, field):
                modified.append(field)
        return modified or None


def iter_patch_leaf_paths(patch: RequirementPatch) -> tuple[str, ...]:
    paths: list[str] = []
    top_fields = _fields_set(patch)
    if "destination" in top_fields:
        paths.append("destination")
    nested = (
        ("budget", patch.budget, "budget"),
        ("party", patch.party, "party"),
        ("rooms", patch.rooms, "rooms"),
        ("constraints", patch.constraints, "constraints"),
        ("preferences", patch.preferences, "preferences"),
    )
    for top_name, model, prefix in nested:
        if top_name in top_fields and model is not None:
            paths.extend(f"{prefix}.{name}" for name in _fields_set(model) if name not in {"total_people"})
    if "time_window" in top_fields and patch.time_window is not None:
        fields = _fields_set(patch.time_window)
        for name in fields - {"arrival", "departure", "hotel_check_in_date", "hotel_check_out_date", "hotel_nights"}:
            paths.append(f"time_window.{name}")
        if "arrival" in fields:
            paths.extend(f"time_window.arrival.{name}" for name in _fields_set(patch.time_window.arrival))
        if "departure" in fields:
            paths.extend(f"time_window.departure.{name}" for name in _fields_set(patch.time_window.departure))
    return tuple(paths)


def update_metadata_for_patch(
    registry: FieldMetadataRegistry,
    patch: RequirementPatch,
    turn_id: UUID,
    *,
    receipts: Sequence[EvidenceReceipt] = (),
    normalizations: Optional[Mapping[str, NormalizationRecord]] = None,
    issues: Sequence[RequirementIssue] = (),
    field_paths: Optional[Iterable[str]] = None,
    derived_fields: Iterable[str] = (),
    pending_confirmation_fields: Iterable[str] = (),
) -> None:
    receipt_map = {receipt.field_path.value: receipt for receipt in receipts}
    normalization_map = dict(normalizations or {})
    issues_by_path: dict[str, list[RequirementIssue]] = {}
    for issue in issues:
        issues_by_path.setdefault(issue.field_path.value, []).append(issue)
    patch_paths = set(field_paths if field_paths is not None else iter_patch_leaf_paths(patch))
    derived = set(derived_fields)
    pending = set(pending_confirmation_fields)
    all_paths = patch_paths | derived | set(receipt_map) | set(normalization_map) | set(issues_by_path)

    for path in sorted(all_paths):
        field_path = RequirementFieldPath(path)
        meta = registry.metadata.setdefault(field_path, FieldMetadata())
        meta.source_turn_id = turn_id
        receipt = receipt_map.get(path)
        if receipt is not None:
            meta.evidence_ref = receipt.evidence_ref
            meta.evidence_digest = receipt.evidence_digest
        if path in normalization_map:
            meta.normalization = normalization_map[path]
        meta.issues = list(issues_by_path.get(path, ()))
        if any(issue.code == "requirement_conflict" for issue in meta.issues):
            meta.status = FieldStatus.CONFLICT
        elif meta.issues:
            meta.status = FieldStatus.INVALID
        elif path in pending:
            meta.status = FieldStatus.PENDING_CONFIRMATION
        elif path in derived:
            meta.status = FieldStatus.DERIVED
        else:
            meta.status = FieldStatus.EXTRACTED
