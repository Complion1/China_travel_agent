"""新模板完整保存质量信息，旧模板保持 main 的规范字节。"""

import hashlib
import json

import pytest
from pydantic import ValidationError

from app.result_formatting import ResultDayArtifact, ResultFormattingLoaded, ResultFormattingService
from app.result_formatting.artifacts import BoundaryResultDayData, BoundaryResultCore
from .fakes import START_DATE, interval, local_time, make_scenario, make_read_request, replace_itinerary, tree
from .test_service import saved_bundle



@pytest.mark.asyncio
@pytest.mark.parametrize("collection,expected", (
    ("1.0", ("d429308d9354095040952dd2e9b02595bb8de5e53bd84ae096eeaaa213795373",
             "dd38fc7b4dd7f7dcb14f43bcd3a5efa17560ded5f85f65e7134b7c0ba21f12ff",
             "52a5fb230b992b74623a39a01bd540006c61f905f70c316ba8e5150e99739e86",
             "7cc7f9d99d5d21d75715ca2a99bca27e5af39fef703cf44dc2fc0574342403f0")),
    ("1.1", ("7d752c956ac7f45642382540df91be85a40bfc747042044b449fb603cea95e02",
             "4e4ede59425a602b2991ead8720e8471b4580302fceb26fb056fece5a9ad5116",
             "0a51ef2552db175a58e8df65f01271d9aab6e5095a17833bb9df31992b61c599",
             "39ad1aa3d1b4e7c03ef884f2d76e07874c7fa1695c239311499c6a0448b906f0")),
))
async def test_v7_bytes_and_markdown_keep_the_original_template(collection, expected):
    scenario = await make_scenario(planning_version="7.0", collection_version=collection)
    prepared, _, index, ref = await saved_bundle(scenario)
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(make_read_request(scenario, ref))
    assert (digest(tree(scenario.itinerary)), digest([tree(day) for day in prepared.days]), digest(tree(index)),
        hashlib.sha256(loaded.markdown.encode()).hexdigest()) == expected


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,expected", (
    ("1.0", ("355d9f7af23794feba58173d9bd1f40c0981de0c684081568d7f18b38da881ff", "9c043cc5702a560f406e859d7c29a86580d9b41ffe50bb5f281cc096a543bbc9", "6ebeac4dc08da2650f391f9ff52866f4b8eb7628a5153640fa609e3348b13871", "34aaf7df55c90d68eb38de334c01832c2da0821ffbf5a9745d7bbb370122c81f")),
    ("1.1", ("8d3e03e520e9bd390227fede547b03dd88d8e439d7e696b3658dcee6f36875e3", "8c79cbfcd261b9820a4b064fad66fe36f4c57d63045138056b3716b23483bc40", "12ba561829f44cf833b9f2a0bae3f6d0ff4ef850df4098191abc09ff23db18a4", "39ad1aa3d1b4e7c03ef884f2d76e07874c7fa1695c239311499c6a0448b906f0")),
))
async def test_legacy_canonical_and_markdown_match_prechange_main(collection, expected):
    scenario = await make_scenario(collection_version=collection)
    prepared, _, index, ref = await saved_bundle(scenario)
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(make_read_request(scenario, ref))
    assert (digest(tree(scenario.itinerary)), digest([tree(day) for day in prepared.days]), digest(tree(index)),
        hashlib.sha256(loaded.markdown.encode()).hexdigest()) == expected
    assert "planned_breaks" not in tree(prepared.days[0])["data"]
    assert "quality_issue_count" not in tree(index)["core"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_issues", (False, True))
@pytest.mark.parametrize("planning_version,template", (("7.0","zh-guide-v3-boundary-quality"),("8.0","zh-guide-v4-daily-quality")))
async def test_quality_template_preserves_source_quality_and_rejects_version_mixing(has_issues, planning_version, template):
    scenario = await make_scenario(planning_version=planning_version, collection_version="1.1")
    data = tree(scenario.itinerary)
    day = data["record"]["draft"]["days"][0]
    day["planned_breaks"] = [{"break_id": "afternoon", "kind": "rest", "reason": "午后适当休息",
        "interval": interval(local_time(START_DATE, 16), local_time(START_DATE, 16, 30))}]
    # M12 must preserve the controller's recorded reminders instead of reclassifying them.
    day["quality_issues"] = [{"date": str(START_DATE), "rule": "unexplained_gap", "minutes": 180,
        "interval": interval(local_time(START_DATE, 15), local_time(START_DATE, 18))}] if has_issues else []
    scenario = await replace_itinerary(scenario, data)
    prepared, _, index, ref = await saved_bundle(scenario)
    assert index.template_version == template
    assert type(index.core) is BoundaryResultCore
    assert index.core.quality_issue_count == int(has_issues)
    assert type(prepared.days[0].data) is BoundaryResultDayData
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(make_read_request(scenario, ref))
    assert isinstance(loaded, ResultFormattingLoaded) and loaded.days == prepared.days
    assert "16:00" in loaded.markdown and "16:30" in loaded.markdown and "休息（规划安排）：午后适当休息" in loaded.markdown
    assert "None" not in loaded.markdown and "同行人员" not in loaded.markdown
    assert ("尚待完善的安排" in loaded.markdown) == has_issues
    if has_issues:
        assert "180 分钟未说明的空档" in loaded.markdown and "不表示这些问题已经解决" in loaded.markdown
    if has_issues:
        assert ("每日安排仍有" in loaded.markdown) == (planning_version == "8.0")
        assert ("抵离日仍有" in loaded.markdown) == (planning_version == "7.0")
    for wrong in ("zh-guide-v2-active-inputs", "zh-guide-v4-daily-quality" if planning_version == "7.0" else "zh-guide-v3-boundary-quality"):
        raw = tree(prepared.days[0])
        raw["template_version"] = wrong
        with pytest.raises(ValidationError):
            ResultDayArtifact.model_validate_json(json.dumps(raw), strict=True)
    raw = tree(prepared.days[0])
    raw["data"].pop("planned_breaks")
    raw["data"].pop("quality_issues")
    with pytest.raises(ValidationError):
        ResultDayArtifact.model_validate_json(json.dumps(raw), strict=True)
