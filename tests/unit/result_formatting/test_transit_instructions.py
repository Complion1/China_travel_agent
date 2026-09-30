"""Selected provider instructions survive persistence without model rewriting."""

import pytest
from pydantic import ValidationError

from app.result_formatting import ResultFormattingLoaded, ResultFormattingService
from app.result_formatting.artifacts import ResultRoute
from app.result_formatting.projection import ProjectionFailure
from app.result_formatting.renderer import render_day, render_header, render_footer
from tests.unit.result_formatting.fakes import make_scenario, make_read_request
from tests.unit.result_formatting.test_renderer import project
from tests.unit.result_formatting.test_service import saved_bundle


STEPS = ("步行到合成甲站", "乘坐合成1号线往乙方向，在合成乙站下车", "换乘合成20路，到合成丙站下车")


@pytest.mark.asyncio
async def test_selected_transit_steps_persist_load_and_render_in_provider_order():
    scenario = await make_scenario(transit_instructions=STEPS)
    prepared, _, _, index_ref = await saved_bundle(scenario)
    # A fresh service reads actual persisted artifacts, with no map/model calls.
    loaded = await ResultFormattingService(artifact_repository=scenario.repository).load(make_read_request(scenario, index_ref))
    assert isinstance(loaded, ResultFormattingLoaded)
    assert loaded.days == prepared.days
    assert "未选中方案" not in loaded.markdown
    assert "1. 步行到合成甲站\n  2. 乘坐合成1号线往乙方向，在合成乙站下车\n  3. 换乘合成20路，到合成丙站下车" in loaded.markdown
    assert "班次与实时运营未核验" in loaded.markdown
    core, days = project(scenario)
    routes = (core.arrival_transfer.route, *days[0].route_legs, core.departure_transfer.route)
    assert all(route.transit_instructions == STEPS and route.alternative_index == 1 for route in routes)


@pytest.mark.asyncio
async def test_old_routes_keep_omitted_field_and_original_rendering():
    scenario = await make_scenario()
    core, days = project(scenario)
    route = days[0].route_legs[0]
    encoded = route.model_dump_json()
    assert "transit_instructions" not in encoded
    assert ResultRoute.model_validate_json(encoded).model_dump_json() == encoded
    assert "乘车顺序" not in render_day(core, days[0])
    legacy = await make_scenario(transit_instructions=())
    old_core, old_days = project(legacy)
    assert "乘车顺序" not in render_header(old_core) + render_day(old_core, old_days[0]) + render_footer(old_core)


@pytest.mark.asyncio
async def test_transit_steps_escape_markup_and_reject_control_text():
    scenario = await make_scenario(transit_instructions=("乘车 <script> & [说明](https://example.test)",))
    core, days = project(scenario)
    text = render_day(core, days[0])
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "[说明](https://" not in text
    unsafe = await make_scenario(transit_instructions=("乘车\x1b指令",))
    core, days = project(unsafe)
    with pytest.raises(ProjectionFailure):
        render_day(core, days[0])


@pytest.mark.asyncio
async def test_steps_cannot_be_attached_to_nontransit_route():
    core, days = project(await make_scenario())
    payload = days[0].route_legs[0].model_dump(mode="python")
    payload["transit_instructions"] = STEPS
    with pytest.raises(ValidationError):
        ResultRoute.model_validate(payload)
