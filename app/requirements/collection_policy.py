"""按任务版本选择需求收集范围，保留历史任务规则。"""

from typing import Literal, TypeAlias


RequirementCollectionVersion: TypeAlias = Literal["1.0", "1.1"]
ACTIVE_COLLECTION_VERSION = "1.1"

_BUDGET_FIELDS = frozenset({"budget.total_cny", "budget.is_approximate"})
_DEFERRED_FIELDS = _BUDGET_FIELDS | frozenset({
    "party.adults_count", "party.children_count", "party.children_ages",
    "party.seniors_count", "party.total_people", "rooms.room_count",
    "rooms.room_type_requirements", "constraints.mobility_constraints",
    "constraints.dietary_restrictions", "inferred_scenarios",
})


def deferred_field_paths(version: RequirementCollectionVersion) -> frozenset[str]:
    return _DEFERRED_FIELDS if version == ACTIVE_COLLECTION_VERSION else _BUDGET_FIELDS
