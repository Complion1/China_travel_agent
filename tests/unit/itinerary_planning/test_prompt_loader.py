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
    boundary_prompt = resources.files("app.itinerary_planning.prompts").joinpath("planning_v10.txt").read_bytes()
    assert hashlib.sha256(boundary_prompt).hexdigest() == "285ac96dbf9fbf47b30329922ba16b7a4dbc28a70fca0b3c13d6929b158b2a94"
    daily_prompt = resources.files("app.itinerary_planning.prompts").joinpath("planning_v11.txt").read_bytes()
    assert hashlib.sha256(daily_prompt).hexdigest() == "d340f0e8d1b52ea1414f744c7aa5b7c4519d2ed877bc7a695f407387f17b61d5"


def test_planning_prompt_is_packaged_utf8_and_hash_locked() -> None:
    resource = load_planning_prompt()
    assert resource.version == PLANNING_PROMPT_VERSION == "planning-v12"
    assert resource.sha256 == PLANNING_PROMPT_SHA256
    for required in ("默认最大窗口07:30～22:00", "当前提供的六个工具", "午餐与晚餐不能同一地点",
                     "先取得需要的候选证据", "补充仅限主行程确有候选缺口", "不为可选备选申请",
                     "不重造ID或交叉配对", "直接复用这些句柄", "精确到分钟"):
        assert required in resource.text


def test_prompt_does_not_contain_external_or_sensitive_instructions() -> None:
    text = load_planning_prompt().text.lower()
    for forbidden in ("api_key", "authorization", ".env", "http://", "https://", "route_steps", "stack trace"):
        assert forbidden not in text


def test_prompt_exposes_the_controller_date_and_route_capacity_rules() -> None:
    text = load_planning_prompt().text
    for required in ("数量=sightseeing_days", "不增加区间外日期", "end_at=null",
                     "3600/5400/7200秒", "7200/10800/14400秒", "优先 public_transit/transit",
                     "不能用驾车冒充公交", "不为形式比较强制每段查询所有模式",
                     "正常调用最多17次", "共享格式修正储备2", "总最多19", "准备2、交通5、提交修正5",
                     "Matrix 每日期每批最多一次、每次最多10地点", "备选可全部为空",
                     "daily_outline", "daily_windows", "remaining_direction_slots",
                     "remaining_direction_queries", "daily_plan_quality_review", "宽裕时按轻松1～2、标准3、紧凑4个景点作为目标",
                     "无须安排到22:00", "长窗口空一天不可交付"):
        assert required in text
    assert "标准恰好" not in text and "固定4" not in text


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
