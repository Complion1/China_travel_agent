from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.agent.schemas import PlaceCandidate, PlaceRole
from app.config import settings


class BaiduMapToolError(RuntimeError):
    pass


@dataclass
class BaiduMapTool:
    """Baidu Map Agent Plan tool adapter.

    This is the Tool/MCP layer in the architecture. The first version calls the
    HTTP endpoints directly; a formal MCP client can be placed behind this same
    facade later.
    """

    token: str | None = None
    base_url: str = "https://api.map.baidu.com/agent_plan/v1"
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = settings.baidu_map_auth_token

    def place(self, user_raw_request: str, region: str, sort: str = "relevance") -> dict[str, Any]:
        return self._get(
            "place",
            {
                "user_raw_request": user_raw_request,
                "region": region,
                "sort": sort,
            },
        )

    def geocoding(self, address: str, region: str | None = None) -> dict[str, Any]:
        params = {"address": address}
        if region:
            params["region"] = region
        return self._get("geocoding", params)

    def direction(self, user_raw_request: str, location: str) -> dict[str, Any]:
        return self._get(
            "direction",
            {
                "user_raw_request": user_raw_request,
                "location": location,
            },
        )

    def weather(self, region: str | None = None, location: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if region:
            params["region"] = region
        if location:
            params["location"] = location
        if not params:
            raise ValueError("weather requires region or location")
        return self._get("weather", params)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.token:
            raise BaiduMapToolError("BAIDU_MAP_AUTH_TOKEN is missing")

        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}/{path}?{query}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:  # pragma: no cover - network behavior
            raise BaiduMapToolError(str(exc)) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BaiduMapToolError(f"invalid json response: {raw[:200]}") from exc

        status = payload.get("status")
        if status not in (0, "0", None):
            message = payload.get("message") or payload.get("msg") or "unknown error"
            raise BaiduMapToolError(f"baidu status={status}: {message}")
        return payload


def compact_place_results(
    payload: dict[str, Any],
    limit: int = 6,
    role: str = PlaceRole.UNKNOWN.value,
) -> list[PlaceCandidate]:
    results = payload.get("results") or []
    if not isinstance(results, list):
        return []

    compact = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        compact.append(PlaceCandidate.from_baidu(item, role=role))
    return compact
