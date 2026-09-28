"""Only the approved M09 candidate-supply targets become M08 requests."""
from pydantic import TypeAdapter

from app.candidate_research import SupplementalGap
from app.itinerary_planning import PlanningGap
from app.durable_execution import DurableExecutionStateError

from .commands import canonical


_TARGETS = {
    "candidate_count": "candidate_count_shortfall", "area_coverage": "area_coverage_missing",
    "meal_coverage": "meal_coverage_missing", "must_resolution": "must_visit_resolution_missing",
    "avoid_resolution": "avoid_resolution_missing", "candidate_details": "candidate_details_missing",
    "replacement": "replacement_candidate_needed",
}
_DIRECTORIES = {
    "hotel_selection": "hotel_selection_refs", "candidate_set": "candidate_set_refs",
    "candidate_evidence": "candidate_evidence_refs", "hub_geocode": "hub_geocode_refs",
    "matrix": "matrix_refs", "directions": "directions_refs",
}


def restore_gaps(artifact):
    """Restore typed public gaps using the exact persisted evidence directory."""
    result = []
    for gap in artifact.record.gaps:
        tree = gap.model_dump(mode="json")
        tree["gap_type"] = tree.pop("summary_type")
        indexes = tree.pop("evidence_indexes")
        tree["evidence_refs"] = []
        for index in indexes:
            directory = getattr(artifact.directories, _DIRECTORIES[index["directory"]])
            tree["evidence_refs"].append(directory[index["index"]].model_dump(mode="json"))
        result.append(TypeAdapter(PlanningGap).validate_json(canonical(tree), strict=True))
    return tuple(result)


def research_gaps(gaps):
    result = []
    for gap in gaps:
        if gap.gap_type != "candidate_supply" or not 1 <= len(gap.evidence_refs) <= 8:
            raise DurableExecutionStateError()
        tree = gap.target.model_dump(mode="json")
        target = tree.pop("target_type")
        if target not in _TARGETS:
            raise DurableExecutionStateError()
        anchor = tree.get("anchor")
        if anchor is not None:
            tree["anchor"] = {"anchor_type": anchor["ref_type"]}
            if anchor["ref_type"] == "candidate":
                tree["anchor"]["uid"] = anchor["uid"]
        tree.update(gap_type=_TARGETS[target], gap_id=gap.gap_id,
            evidence_refs=[ref.model_dump(mode="json") for ref in gap.evidence_refs])
        result.append(TypeAdapter(SupplementalGap).validate_json(canonical(tree), strict=True))
    if not result:
        raise DurableExecutionStateError()
    return tuple(result)
