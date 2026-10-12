"""Frozen requirement text is data, never a provider fact or an instruction."""

from dataclasses import dataclass
from collections.abc import Sequence


class RequirementContextError(ValueError):
    """Frozen IDs and their approved context cannot be safely projected."""


@dataclass(frozen=True, slots=True)
class RequirementContext:
    kind: str
    group_id: str
    item_ids: tuple[str, ...]
    name: str

    def project(self) -> dict[str, object]:
        if self.kind not in {"must_visit", "avoid"} or not self.name.strip() or not self.item_ids:
            raise RequirementContextError("invalid requirement context")
        return {"requirement_kind": self.kind, "requirement_group_id": self.group_id,
                "requirement_item_ids": self.item_ids, "confirmed_name": self.name}


def project_requirements(specs: Sequence[tuple[str, str, tuple[str, ...]]],
                         directory: Sequence[RequirementContext]) -> tuple[dict[str, object], ...]:
    if tuple((item.kind, item.group_id, item.item_ids) for item in directory) != tuple(specs):
        raise RequirementContextError("requirement context does not match frozen IDs")
    if len({item.group_id for item in directory}) != len(directory):
        raise RequirementContextError("duplicate requirement group context")
    return tuple(item.project() for item in directory)
