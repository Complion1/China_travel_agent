import hashlib
from importlib import resources

import pytest

import app.itinerary_planning.prompt_loader as prompt_loader
from app.itinerary_planning.errors import ItineraryPlanningError
from app.itinerary_planning.prompt_loader import (
    PLANNING_PROMPT_SHA256,
    PLANNING_PROMPT_VERSION,
    load_planning_prompt,
)


def test_previous_prompt_remains_immutable():
    previous = resources.files("app.itinerary_planning.prompts").joinpath("planning_v3.txt").read_bytes()
    assert hashlib.sha256(previous).hexdigest() == "cc9d5816a91fde13eeaea17d6925ffa5ea7794ba6041956913ce45f08f187934"
    minute_prompt = resources.files("app.itinerary_planning.prompts").joinpath("planning_v8.txt").read_bytes()
    assert hashlib.sha256(minute_prompt).hexdigest() == "4d3526665d1831cc06b14164bb8fcc1a166d04ac10e823077f24a509c7382f16"


def test_planning_prompt_is_packaged_utf8_and_hash_locked() -> None:
    resource = load_planning_prompt()
    assert resource.version == PLANNING_PROMPT_VERSION == "planning-v9"
    assert resource.sha256 == PLANNING_PROMPT_SHA256
    assert "普通日开放区间固定为当日 07:30–22:00" in resource.text
    assert "backup_distance_exceeded" in resource.text
    assert "仅缺备选时不请求补研" in resource.text
    assert "午餐和晚餐不能是同一地点" in resource.text
    assert "backup_candidate_duplicate" in resource.text
    assert "remaining_direction_slots" in resource.text
    assert "meal_intent_mismatch" in resource.text
    assert "restaurant 角色本身不代表同时支持两餐" in resource.text
    assert "get_candidate_evidence" in resource.text
    assert "request_candidate_supplement" in resource.text
    assert "leg_id 必须原样复用该 directions_handle 所属 Observation 返回的 leg_id" in resource.text
    assert "43分钟预留50分钟" in resource.text
    assert "活动及路线出发时刻仍可使用任意整数分钟" in resource.text


def test_prompt_does_not_contain_external_or_sensitive_instructions() -> None:
    text = load_planning_prompt().text.lower()
    for forbidden in ("api_key", "authorization", ".env", "http://", "https://", "route_steps", "stack trace"):
        assert forbidden not in text


def test_prompt_exposes_the_controller_date_and_route_capacity_rules() -> None:
    text = load_planning_prompt().text
    assert "数量必须等于 sightseeing_days" in text
    assert "不得额外创建 transit_only day" in text
    assert "end_at 填 null" in text
    assert "3600/5400/7200 秒" in text
    assert "7200/10800/14400 秒" in text
    assert "抵达、离开接驳和日内各段路线均优先公共交通，网约车和步行其次" in text
    assert "公交或地铁组合使用 public_transit/transit" in text
    assert "calculate_route_matrix 不支持 transit" in text
    assert "先为尚未覆盖的主路线保留 Directions 槽位" in text
    assert "抵达和离开接驳统一使用 ride_hailing/driving" not in text
    assert "标准恰好 3 个" in text
    assert "规划最多 17 次逻辑模型调用" in text
    assert "立即构造最小主行程并提交" in text
    assert "同一业务迭代中每个日期最多调用一次 Matrix" in text
    assert "remaining_matrix_slots" in text
    assert "主行程全部硬约束成立即可提交" in text
    assert "backups=[] 完全合法" in text


def test_prompt_validation_fails_closed_for_type_hash_encoding_and_content(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(Exception):
        prompt_loader._validate_prompt_bytes("not-bytes")
    with pytest.raises(Exception):
        prompt_loader._validate_prompt_bytes(b"different")

    invalid_utf8 = b"\xff"
    monkeypatch.setattr(prompt_loader, "PLANNING_PROMPT_SHA256", "sha256:" + hashlib.sha256(invalid_utf8).hexdigest())
    with pytest.raises(Exception):
        prompt_loader._validate_prompt_bytes(invalid_utf8)
    for payload in (b"  ", b"safe\x00text"):
        monkeypatch.setattr(prompt_loader, "PLANNING_PROMPT_SHA256", "sha256:" + hashlib.sha256(payload).hexdigest())
        with pytest.raises(Exception):
            prompt_loader._validate_prompt_bytes(payload)

    def missing_resource(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError()

    monkeypatch.setattr(prompt_loader.resources, "files", missing_resource)
    with pytest.raises(Exception):
        prompt_loader._read_prompt_bytes()


def test_marker_error_is_immutable_and_zero_sensitive() -> None:
    error = ItineraryPlanningError()
    assert str(error) == "itinerary_planning_error"
    assert "code='itinerary_planning_error'" in repr(error)
    with pytest.raises(AttributeError):
        _ = error.__dict__
    with pytest.raises(AttributeError):
        error.extra = "secret"
    error.__traceback__ = None
    with pytest.raises(AttributeError):
        del error.extra
