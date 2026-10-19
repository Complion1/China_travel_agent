"""用实际 M12 产物核对新版结果的浏览器呈现与无损导出。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app.web_application import WebResultView
from .test_demo_pages import ui_projection, route_projection


async def result_fixture():
    from app.result_formatting import ResultFormattingService
    from tests.unit.result_formatting.fakes import make_scenario, tree, replace_itinerary, interval, local_time, START_DATE, make_read_request
    from tests.unit.result_formatting.test_service import saved_bundle
    case = await make_scenario(planning_version="8.0", collection_version="1.1")
    payload = tree(case.itinerary)
    day = payload["record"]["draft"]["days"][0]
    day["planned_breaks"] = [dict(break_id="rest", kind="rest", reason="午后适当休息",
        interval=interval(local_time(START_DATE, 16), local_time(START_DATE, 16, 30)))]
    day["quality_issues"] = [dict(date=str(START_DATE), rule="unexplained_gap", minutes=180,
        interval=interval(local_time(START_DATE, 15), local_time(START_DATE, 18)))]
    case = await replace_itinerary(case, payload)
    _, _, _, ref = await saved_bundle(case)
    loaded = await ResultFormattingService(artifact_repository=case.repository).load(make_read_request(case, ref))
    return WebResultView.model_validate_json(json.dumps(dict(schema_version="1.0", result_schema_version="2.0",
        markdown=loaded.markdown, days=tuple(dict(schema_version="1.0", ordinal=item.data.day_no, markdown=item.markdown)
            for item in loaded.days), delivery_status="degraded", terminal_code="workflow_degraded")))


@pytest.mark.parametrize("width", (1280, 390))
def test_boundary_reminders_and_breaks_survive_refresh_copy_download(page, origin, width):
    # This presentation fixture comes from the production formatter. Existing
    # workflow browser tests separately exercise the full graph with fake suppliers.
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(lambda: asyncio.run(result_fixture())).result(timeout=20)
    run_id = "00000000-0000-4000-8000-000000000090"
    view = ui_projection(run_id, activity="terminal", lifecycle="closed", phase="output_formatting",
        delivery_status="degraded", terminal_code="workflow_degraded", finalized_at="2026-08-20T00:01:00Z")
    view["result_available"] = True
    route_projection(page, run_id, view)
    page.route(f"**/api/runs/{run_id}/result", lambda route: route.fulfill(status=200,
        content_type="application/json", body=result.model_dump_json()))
    page.set_viewport_size(dict(width=width, height=920))
    page.goto(origin + f"/tasks/{run_id}")
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#result-days")).to_contain_text("休息（规划安排）：午后适当休息")
    expect(page.locator("#result-days")).to_contain_text("180 分钟未说明的空档")
    expect(page.locator("#result-notes")).to_contain_text("不表示这些问题已经解决")
    page.reload()
    expect(page.locator("#result-days")).to_contain_text("尚待完善的安排")
    assert page.locator("#result-markdown").text_content() == result.markdown
    page.evaluate("Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{globalThis.boundaryCopied=text}},configurable:true})")
    page.locator("#copy-result").click()
    page.wait_for_function("globalThis.boundaryCopied!==undefined")
    assert page.evaluate("globalThis.boundaryCopied") == result.markdown
    with page.expect_download() as download:
        page.locator("#download-result").click()
    assert Path(download.value.path()).read_text(encoding="utf-8") == result.markdown
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / f"boundary-guide-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
