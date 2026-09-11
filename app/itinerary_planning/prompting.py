"""安全的 typed planning context、opaque handle directory 与 Prompt 渲染。"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Callable, Iterable

from app.baidu_map import BaiduCoordinate
from app.llm_runtime import PromptEnvelope
from app.requirements import ConfirmedTravelRequirement

from .models import (
    CandidateLocationRef,
    Handle,
    HotelLocationRef,
    HubLocationRef,
    PlanningLocationRef,
)
from .prompt_loader import PromptResource, load_planning_prompt


def _safe_label(value: str | None, *, limit: int = 80) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("label must be text")
    value = " ".join(value.strip().split())
    return value[:limit] or None


@dataclass(frozen=True, slots=True)
class HandleEntry:
    handle: str
    kind: str
    value: object
    coordinate: BaiduCoordinate | None
    label: str | None


class HandleDirectory:
    """Run-local opaque handle table; callers receive typed values only by kind."""

    __slots__ = ("_entries", "_counters")

    def __init__(self) -> None:
        self._entries: dict[str, HandleEntry] = {}
        self._counters: dict[str, int] = {}

    def _new_handle(self, kind: str) -> str:
        ordinal = self._counters.get(kind, 0)
        self._counters[kind] = ordinal + 1
        return f"h-{kind}-{ordinal:04d}"

    def add(
        self,
        *,
        kind: str,
        value: object,
        coordinate: BaiduCoordinate | None = None,
        label: str | None = None,
        handle: str | None = None,
    ) -> str:
        if type(kind) is not str or not kind or any(char.isspace() for char in kind):
            raise ValueError("invalid handle kind")
        selected = handle or self._new_handle(kind)
        if type(selected) is not str or not selected.strip() or len(selected) > 128:
            raise ValueError("invalid handle")
        if selected in self._entries:
            raise ValueError("duplicate handle")
        if coordinate is not None and not isinstance(coordinate, BaiduCoordinate):
            raise TypeError("coordinate must use the public map DTO")
        self._entries[selected] = HandleEntry(
            selected, kind, value, coordinate, _safe_label(label)
        )
        return selected

    def add_location(
        self,
        ref: PlanningLocationRef,
        coordinate: BaiduCoordinate,
        *,
        label: str | None = None,
        handle: str | None = None,
    ) -> str:
        if not isinstance(ref, (CandidateLocationRef, HotelLocationRef, HubLocationRef)):
            raise TypeError("location must use a Module 09 location ref")
        kind = {"candidate": "candidate", "hotel": "hotel", "hub": "hub"}[ref.ref_type]
        return self.add(
            kind=kind,
            value=ref,
            coordinate=coordinate,
            label=label,
            handle=handle,
        )

    def require(self, handle: str, *, kind: str | None = None) -> HandleEntry:
        if type(handle) is not str or handle not in self._entries:
            raise ValueError("unknown or expired handle")
        entry = self._entries[handle]
        if kind is not None and entry.kind != kind:
            raise ValueError("handle kind mismatch")
        return entry

    def require_location(self, handle: str) -> HandleEntry:
        entry = self.require(handle)
        if entry.kind not in {"candidate", "hotel", "hub"} or entry.coordinate is None:
            raise ValueError("handle is not a located planning object")
        return entry

    def snapshot(self) -> tuple[tuple[str, str, str | None], ...]:
        return tuple(
            (entry.handle, entry.kind, entry.label)
            for entry in self._entries.values()
        )

    def __contains__(self, handle: object) -> bool:
        return type(handle) is str and handle in self._entries

    def __len__(self) -> int:
        return len(self._entries)


@dataclass(frozen=True, slots=True)
class PlanningPromptSnapshot:
    planning_context_digest: str
    destination: str
    sightseeing_start_date: str | None
    sightseeing_end_date: str | None
    sightseeing_days: int
    fixed_pace: str | None
    agent_decides_pace: bool
    endpoints: tuple[str, ...]
    handles: tuple[tuple[str, str, str | None], ...]
    objectives: tuple[str, ...]
    confirmed_data: str = "{}"


def _confirmed_snapshot(
    confirmed: ConfirmedTravelRequirement,
    *,
    planning_context_digest: str,
    directory: HandleDirectory,
    objectives: Iterable[str] = (),
) -> PlanningPromptSnapshot:
    requirement = confirmed.requirement
    window = requirement.time_window
    destination = requirement.destination
    if destination is None:
        raise ValueError("confirmed destination is required")
    days = window.sightseeing_days
    if days is None:
        raise ValueError("confirmed sightseeing days are required")
    preferences = requirement.preferences
    return PlanningPromptSnapshot(
        planning_context_digest=planning_context_digest,
        destination=_safe_label(destination, limit=64) or destination,
        sightseeing_start_date=window.sightseeing_start_date,
        sightseeing_end_date=window.sightseeing_end_date,
        sightseeing_days=days,
        fixed_pace=preferences.pace.value if preferences.pace is not None else None,
        agent_decides_pace=preferences.agent_decides_pace,
        endpoints=tuple(
            endpoint
            for endpoint, value in (
                ("arrival", window.arrival.hub_type),
                ("departure", window.departure.hub_type),
            )
            if value is not None
        ),
        handles=directory.snapshot(),
        objectives=tuple(_safe_label(item, limit=80) or "" for item in objectives),
        confirmed_data=json.dumps({
            "arrival": window.arrival.model_dump(mode="json"),
            "departure": window.departure.model_dump(mode="json"),
            "must_visit_places": preferences.must_visit_places,
            "avoid_places": preferences.avoid_places,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def build_prompt_snapshot(
    confirmed: ConfirmedTravelRequirement,
    *,
    planning_context_digest: str,
    directory: HandleDirectory,
    objectives: Iterable[str] = (),
) -> PlanningPromptSnapshot:
    return _confirmed_snapshot(
        confirmed,
        planning_context_digest=planning_context_digest,
        directory=directory,
        objectives=objectives,
    )


def render_prompt_context(snapshot: PlanningPromptSnapshot) -> str:
    """Render only bounded typed fields; no raw user/provider payload is accepted."""

    # Dynamic values are data, not prompt instructions.  Explicit JSON
    # boundaries make that distinction visible to the model and to security
    # scanners without relying on keyword blacklists.
    context = {
        "context_digest": snapshot.planning_context_digest,
        "destination": snapshot.destination,
        "sightseeing_days": snapshot.sightseeing_days,
        "sightseeing_start_date": snapshot.sightseeing_start_date,
        "sightseeing_end_date": snapshot.sightseeing_end_date,
        "fixed_pace": snapshot.fixed_pace,
        "agent_decides_pace": snapshot.agent_decides_pace,
        "confirmed_endpoints": snapshot.endpoints,
        "handles": tuple(
            {"handle": handle, "kind": kind, "label": label}
            for handle, kind, label in snapshot.handles
        ),
        "objectives": snapshot.objectives,
        "confirmed": json.loads(snapshot.confirmed_data),
    }
    encoded = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "BEGIN_TYPED_PLANNING_DATA\n" + encoded + "\nEND_TYPED_PLANNING_DATA"


def make_planning_prompt(
    confirmed: ConfirmedTravelRequirement,
    *,
    planning_context_digest: str,
    directory: HandleDirectory,
    objectives: Iterable[str] = (),
    resource: PromptResource | None = None,
) -> PromptEnvelope:
    prompt = resource or load_planning_prompt()
    snapshot = build_prompt_snapshot(
        confirmed,
        planning_context_digest=planning_context_digest,
        directory=directory,
        objectives=objectives,
    )
    return PromptEnvelope(
        system_text=prompt.text,
        user_text=render_prompt_context(snapshot),
    )


def handle_value(directory: HandleDirectory, handle: str, *, kind: str | None = None) -> object:
    return directory.require(handle, kind=kind).value


async def invoke_reader(reader: Callable[[tuple[str, ...]], object], handles: tuple[str, ...]) -> object:
    result = reader(handles)
    return await result if inspect.isawaitable(result) else result


__all__ = ()
