"""新旧结果模板均可保存、完整读取，并保持各自的确定性。"""

import pytest
from pydantic import ValidationError

from app.result_formatting import ResultFormattingLoaded, ResultFormattingPrepared, ResultFormattingService, ResultIndexArtifact
from .fakes import make_read_request, make_scenario, save_result_days, save_result_index


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_version,template", [("1.0", "zh-guide-v1"), ("1.1", "zh-guide-v2-active-inputs")])
async def test_new_and_legacy_templates_roundtrip_without_rewriting_sources(collection_version, template):
    scenario = await make_scenario(days=3, collection_version=collection_version)
    source = scenario.itinerary.model_dump_json()
    service = ResultFormattingService(artifact_repository=scenario.repository)
    prepared = await service.prepare(scenario.request)
    assert type(prepared) is ResultFormattingPrepared
    assert {day.template_version for day in prepared.days} == {template}
    day_refs = await save_result_days(scenario, prepared.days)
    ready = await service.build_index(scenario.request, day_refs=day_refs)
    assert ready.outcome_type == "index_ready"
    index_ref = await save_result_index(scenario, ready.index)
    loaded = await service.load(make_read_request(scenario, index_ref))
    assert type(loaded) is ResultFormattingLoaded
    assert loaded.index.template_version == template
    assert loaded.days == prepared.days
    assert (await service.prepare(scenario.request)).days == prepared.days
    assert scenario.itinerary.model_dump_json() == source
    assert "少换乘" in loaded.markdown and "晚一点出发" in loaded.markdown
    assert "## 费用核验状态" in loaded.markdown and "## 来源清单" in loaded.markdown
    if collection_version == "1.1":
        requirement = loaded.index.core.requirement
        assert requirement.total_people is None and requirement.room_count is None
        assert "博物馆参观两小时" in loaded.markdown
        assert all(text not in loaded.markdown for text in ("同行人员", "间房", "- 房型：", "- 行动限制：", "- 饮食禁忌：", "用户总预算"))
        assert "3 天" in loaded.markdown and "4 晚" in loaded.markdown
        assert "None" not in loaded.markdown
        with pytest.raises(ValidationError):
            ResultIndexArtifact.model_validate({**loaded.index.model_dump(mode="python"), "template_version": "zh-guide-v1"}, strict=True)
    else:
        assert "同行人员：共 3 人" in loaded.markdown and "儿童年龄：8 岁" in loaded.markdown
        assert "- 房型：家庭房。" in loaded.markdown and "- 饮食禁忌：不吃辣。" in loaded.markdown
