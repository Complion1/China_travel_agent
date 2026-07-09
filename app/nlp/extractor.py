from __future__ import annotations

import re

from app.agent.schemas import TravelRequirement


STYLE_ALIASES = {
    "自然": "nature",
    "风景": "nature",
    "山水": "nature",
    "美食": "food",
    "吃": "food",
    "历史": "history",
    "文化": "history",
    "亲子": "family",
    "带娃": "family",
    "情侣": "couple",
    "拍照": "photo",
    "轻松": "relaxed",
    "慢": "relaxed",
    "特种兵": "intense",
}


class RequirementExtractor:
    """Rule-based extractor placeholder.

    Replace with a structured-output LLM extractor after the harness is stable.
    """

    def extract(self, text: str) -> TravelRequirement:
        return TravelRequirement(
            city=self._extract_city(text),
            days=self._extract_days(text),
            styles=self._extract_styles(text),
            arrival_place=self._extract_arrival_place(text),
            pace=self._extract_pace(text),
            transport_preference=self._extract_transport_preference(text),
            hotel_preference=self._extract_hotel_preference(text),
        )

    def _extract_city(self, text: str) -> str | None:
        explicit = re.search(r"去([\u4e00-\u9fff]{2,8})(?:玩|旅游|旅行|出差|$)", text)
        if explicit:
            return explicit.group(1)
        city = re.search(r"([\u4e00-\u9fff]{2,8})(?:玩|旅游|旅行|攻略)", text)
        if city:
            return city.group(1)
        return None

    def _extract_days(self, text: str) -> int | None:
        number_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
        digit = re.search(r"(\d+)\s*(?:天|日)", text)
        if digit:
            return int(digit.group(1))
        chinese = re.search(r"([一二两三四五六七])\s*(?:天|日)", text)
        if chinese:
            return number_map[chinese.group(1)]
        return None

    def _extract_styles(self, text: str) -> list[str]:
        styles = []
        for key, value in STYLE_ALIASES.items():
            if key in text and value not in styles:
                styles.append(value)
        return styles or ["classic"]

    def _extract_arrival_place(self, text: str) -> str | None:
        candidates = re.findall(
            r"(?:到|抵达|落地|从|高铁到|火车到)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,12}(?:机场|火车站|高铁站|东站|西站|南站|北站))",
            text,
        )
        return candidates[0] if candidates else None

    def _extract_pace(self, text: str) -> str:
        if "特种兵" in text or "紧凑" in text:
            return "intense"
        if "轻松" in text or "慢" in text or "不要太累" in text:
            return "relaxed"
        return "normal"

    def _extract_transport_preference(self, text: str) -> str:
        if "公共交通" in text or "地铁" in text:
            return "public_transit"
        if "打车" in text or "出租" in text:
            return "taxi"
        if "自驾" in text:
            return "driving"
        return "public_transit_and_taxi"

    def _extract_hotel_preference(self, text: str) -> str:
        if "酒店" in text or "住宿" in text or "住" in text:
            return "user_mentioned_hotel_preference"
        return "convenient_transport_mid_range"
