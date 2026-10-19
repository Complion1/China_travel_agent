"""M24 presentation contracts. Routed network cases are separate from real PG tests."""
import json
import os
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect
from tests.browser.web_application.test_demo_pages import (
    capture_sse, refresh_projection, ui_projection, route_projection,
)


RUN = "00000000-0000-4000-8000-000000000124"


def snapshot(page, origin, **changes):
    view = ui_projection(RUN, **changes)
    route_projection(page, RUN, view)
    page.goto(origin + "/tasks/" + RUN)
    expect(page.locator("#workflow")).to_be_visible()
    return view


def record(sequence):
    return dict(sequence=sequence, occurred_at="2026-10-16T01:00:00Z",
        body=dict(schema_version="1.0", kind="place_observation", phase="candidate_research",
            status="observed", title="地点查询结果", facts=[dict(label="地点", value=f"已核验地点 {sequence}")], trip=None))


def progress(page, respond):
    def route(request):
        value = respond(request.request.url)
        request.fulfill(status=200, content_type="application/json", body=json.dumps(value))
    page.route(f"**/api/runs/{RUN}/progress?*", route)


def packet(items=(), *, latest=None, next_after=None, coverage="recorded", settled=False):
    return dict(schema_version="1.0", run_id=RUN, items=list(items), next_after=next_after,
        latest_sequence=latest if latest is not None else max([item["sequence"] for item in items], default=0),
        coverage=coverage, settled=settled)


def shoot(page, name):
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / f"m24-{name}.png"), full_page=True)


@pytest.mark.parametrize("path", ["/", "/history", "/samples"])
def test_b24_01_shared_workspace_desktop_mobile(page, origin, path):
    page.set_viewport_size(dict(width=1440, height=1000))
    page.goto(origin + path)
    expect(page.locator(".site-header nav a")).to_have_count(3)
    expect(page.locator(".site-header")).to_have_css("position", "fixed")
    assert page.locator("main").bounding_box()["x"] >= 190
    if path == "/": shoot(page, "home-desktop")
    page.set_viewport_size(dict(width=390, height=844))
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.locator("nav a").first.focus()
    expect(page.locator("nav a").first).to_be_focused()
    if path == "/": shoot(page, "home-mobile")


def test_b24_02_future_example_clear_and_explicit_date_confirmation(page, origin):
    page.goto(origin + "/")
    expect(page.locator("#fill-example")).to_be_enabled()
    text = page.locator("#trip-input").input_value()
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    assert len(dates) == 4 and dates[0] == dates[2] and dates[1] == dates[3]
    from datetime import date
    assert (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days == 2
    assert "合肥南站" in text and "09:00" in text and "20:00" in text
    expect(page.locator("#date-confirm")).not_to_be_checked()
    page.locator("#date-confirm").check()
    page.locator("#trip-input").fill(text + "\n喜欢博物馆")
    page.locator("#clear-input").click()
    expect(page.locator("#date-confirm")).not_to_be_checked()
    expect(page.locator("#trip-input")).to_have_value("")
    expect(page.locator("#trip-input")).to_be_focused()
    page.locator("#fill-example").click()
    expect(page.locator("#trip-input")).to_have_value(text)
    expect(page.locator("#date-confirm")).not_to_be_checked()


def test_b24_03_confirmation_form_survives_snapshots_and_details(page, origin):
    view = ui_projection(RUN, activity="awaiting_user", lifecycle="open", phase="requirements", pause_generation=1, delivery_status="awaiting_user")
    view.update(actions=["confirm"], card=dict(kind="confirmation", title="确认旅行需求",
        paragraphs=[], facts=[], choices=[], schema_version="1.0"))
    route_projection(page, RUN, view)
    progress(page, lambda _: packet())
    page.goto(origin + "/tasks/" + RUN)
    page.locator("#revision-input").fill("请保留正在填写的修改")
    page.locator('[data-step="requirements"]').click()
    page.locator("#refresh-task").click()
    expect(page.locator("#revision-input")).to_have_value("请保留正在填写的修改")
    assert page.locator("#interaction").bounding_box()["y"] < page.locator("#process-overview").bounding_box()["y"]
    page.locator("#close-details").click()
    expect(page.locator('[data-step="requirements"]')).to_be_focused()


@pytest.mark.parametrize("kind", ("clarification", "confirmation"))
def test_same_snapshot_keeps_form_focus_and_subsequent_typing(page, origin, kind):
    capture_sse(page)
    view = ui_projection(RUN, activity="awaiting_user", phase="requirements",
        pause_generation=1, delivery_status="awaiting_user")
    card = dict(kind=kind, title="旅行需求", paragraphs=[], facts=[], choices=[], schema_version="1.0")
    if kind == "clarification":
        card["questions"] = [dict(schema_version="1.0", field="time_window.departure.hub_name",
            kind="text", label="离开杭州的机场/车站")]
        selector = "#clarify-time_window-departure-hub_name"
    else:
        selector = "#revision-input"
    view.update(actions=["clarify" if kind == "clarification" else "confirm"], card=card)
    route_projection(page, RUN, view)
    progress(page, lambda _: packet())
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
    page.goto(origin + "/tasks/" + RUN)
    page.wait_for_function("() => document.getElementById('process-overview').previousElementSibling === document.getElementById('interaction')")
    field = page.locator(selector)
    field.fill("杭州萧山")
    expect(field).to_be_focused()
    with page.expect_response(lambda response: response.request.method == "GET"
            and response.url.endswith("/api/runs/" + RUN)):
        refresh_projection(page)
    # 等状态读取后的页面更新完成，再验证焦点和真实键盘输入。
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(field).to_be_focused(timeout=2000)
    page.keyboard.insert_text("机场")
    expect(field).to_have_value("杭州萧山机场")
    assert writes == []


def test_b24_04_hotel_whole_card_and_keyboard_selection(page, origin):
    view = ui_projection(RUN, activity="awaiting_user", lifecycle="open", phase="hotel_selection", pause_generation=2, delivery_status="awaiting_user")
    view.update(actions=["hotel"], card=dict(kind="hotel", title="选择酒店", paragraphs=[], facts=[],
        choices=[dict(ordinal=i, name=f"示例酒店{i}", address="真实查询地址", facts=[], schema_version="1.0") for i in (1, 2)],
        schema_version="1.0"))
    route_projection(page, RUN, view)
    page.goto(origin + "/tasks/" + RUN)
    page.locator(".hotel-card").last.locator("p").click()
    expect(page.locator("input[name=hotel]").last).to_be_checked()
    page.locator("input[name=hotel]").last.press("ArrowLeft")
    expect(page.locator("input[name=hotel]").first).to_be_checked()
    for width, name in ((1440, "hotel-desktop"), (390, "hotel-mobile")):
        page.set_viewport_size(dict(width=width, height=1000))
        shoot(page, name)


def test_b24_05_incremental_pages_deduplicate_and_keep_expansion(page, origin):
    calls = []
    def respond(url):
        calls.append(url)
        if url.endswith("after=0"): return packet([record(1)], latest=2, next_after=1)
        if url.endswith("after=1"): return packet([record(1), record(2)], latest=2)
        return packet(latest=2)
    progress(page, respond)
    snapshot(page, origin)
    node = page.locator('[data-step="research"]')
    node.click()
    expect(page.locator(".fact-record")).to_have_count(2)
    page.locator(".fact-record summary").first.click()
    page.locator("#reload-progress").click()
    expect(page.locator(".fact-record")).to_have_count(2)
    assert page.locator(".fact-record").first.evaluate("node=>node.open")
    assert calls[0].endswith("after=0") and calls[1].endswith("after=1")
    page.locator('[data-step="planning"]').click()
    expect(page.locator("#progress-empty")).to_contain_text("这个阶段尚无")
    node.click()
    assert page.locator(".fact-record").first.evaluate("node=>node.open")
    for width, name in ((1440, "details-desktop"), (390, "details-mobile")):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        shoot(page, name)


@pytest.mark.parametrize("coverage", ["unavailable", "limited"])
def test_b24_06_missing_and_unsaved_records_are_explicit(page, origin, coverage):
    progress(page, lambda _: packet(coverage=coverage))
    snapshot(page, origin)
    page.locator('[data-step="research"]').click()
    expect(page.locator("#progress-state")).to_contain_text("没有可读取" if coverage == "unavailable" else "部分记录未保存")
    expect(page.locator("#progress-empty")).to_be_visible()


def test_b24_07_read_failure_preserves_facts_and_manual_retry(page, origin):
    failing = [False]
    def respond(route):
        route.fulfill(status=503 if failing[0] else 200, content_type="application/json",
            body=json.dumps(dict(message="服务暂不可用") if failing[0] else packet([record(1)])))
    page.route(f"**/api/runs/{RUN}/progress?*", respond)
    snapshot(page, origin)
    page.locator('[data-step="research"]').click()
    expect(page.locator(".fact-record")).to_have_count(1)
    failing[0] = True
    page.locator("#reload-progress").click()
    expect(page.locator("#progress-state")).to_contain_text("保留上次读取")
    expect(page.locator(".fact-record")).to_have_count(1)
    failing[0] = False
    page.locator("#reload-progress").click()
    expect(page.locator("#progress-state")).to_contain_text("最近读取")


def test_b24_08_terminal_waits_for_late_record_and_settled_watermark(page, origin):
    calls = []
    values = [packet([record(1)]), packet([record(2)], latest=2, settled=True)]
    progress(page, lambda url: (calls.append(url), values[min(len(calls)-1, 1)])[1])
    page.clock.install()
    snapshot(page, origin, activity="terminal", lifecycle="closed", phase="candidate_research",
        delivery_status="failed", terminal_code="workflow_provider_failed", finalized_at="2026-10-16T02:00:00Z")
    page.locator('[data-step="research"]').click()
    expect(page.locator(".fact-record")).to_have_count(1)
    page.clock.fast_forward(5000)
    expect(page.locator(".fact-record")).to_have_count(2)
    expect(page.locator("#progress-state")).to_contain_text("终态记录已收束")
    count = len(calls)
    page.clock.fast_forward(65000)
    assert len(calls) == count == 2


def test_b24_09_terminal_unsettled_limit_allows_manual_retry(page, origin):
    calls = []
    progress(page, lambda url: (calls.append(url), packet())[1])
    page.clock.install()
    snapshot(page, origin, activity="terminal", lifecycle="closed", phase="candidate_research",
        delivery_status="failed", terminal_code="workflow_provider_failed", finalized_at="2026-10-16T02:00:00Z")
    page.locator('[data-step="research"]').click()
    expect(page.locator("#progress-state")).to_contain_text("最近读取")
    for _ in range(11):
        page.clock.fast_forward(5000)
        page.wait_for_timeout(50)
    expect(page.locator("#progress-state")).to_contain_text("已暂停自动读取")
    assert len(calls) == 12
    page.clock.fast_forward(30000)
    assert len(calls) == 12
    page.locator("#reload-progress").click()
    expect(page.locator("#progress-state")).to_contain_text("最近读取")
    assert len(calls) == 13


def test_b24_10_keyboard_focus_close_and_background_polling(page, origin):
    calls = []
    progress(page, lambda url: (calls.append(url), packet())[1])
    page.clock.install()
    snapshot(page, origin)
    node = page.locator('[data-step="research"]')
    node.focus()
    node.press("Enter")
    expect(page.locator("#process-details")).to_be_visible()
    page.locator("#close-details").focus()
    page.locator("#close-details").press("Escape")
    expect(node).to_be_focused()
    node.press("ArrowRight")
    expect(page.locator('[data-step="planning"]')).to_be_focused()
    node.click()
    expect(page.locator("#progress-state")).to_contain_text("最近读取")
    count = len(calls)
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
    page.clock.fast_forward(30000)
    assert len(calls) == count
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("document.querySelector('#progress-state').textContent.includes('最近读取')")
    page.locator("#close-details").click()
    count = len(calls)
    page.clock.fast_forward(30000)
    assert len(calls) == count


def test_b24_11_completed_guide_priority_day_reading_transit_and_original_bytes(page, origin):
    header="# 合肥旅行攻略\n\n费用尚未核验。"
    day1=("## 第 1 天｜2026-10-23｜城市初览\n\n### 主行程\n\n"
        "- 2026-10-23 09:00—2026-10-23 09:30（北京时间）｜交通：酒店 → 博物馆；公共交通；日程预留 30 分钟；费用未核验。\n\n"
        "  乘车顺序（查询时路线说明，班次与实时运营未核验）：\n\n  1. 在酒店站乘车\n  2. 在博物馆站下车\n"
        "- 2026-10-23 10:00—2026-10-23 11:00（北京时间）｜景点：博物馆；营业时间未核验〔来源 1〕。")
    day2="## 第 2 天｜2026-10-24｜返程\n\n本日未安排游览。"
    footer="## 出行前复核\n\n返程须预留交通时间。\n\n## 来源清单\n\n- 〔来源 1〕百度地图｜获取时间：2026-10-16。"
    original="\n\n".join((header,day1,day2,footer))+"\n"
    view=ui_projection(RUN,activity="terminal",lifecycle="closed",phase="output_formatting",
        delivery_status="degraded",terminal_code="workflow_degraded",finalized_at="2026-10-16T02:00:00Z")
    view.update(result_available=True,trip=dict(city="合肥",start_date="2026-10-23",end_date="2026-10-24",days=2))
    route_projection(page,RUN,view)
    data=dict(schema_version="1.0", result_schema_version="2.0", markdown=original,
        days=[dict(schema_version="1.0",ordinal=i,markdown=md,reading=dict(date=date,theme=theme,visit_count=count,
            route_count=route,transport_minutes=minutes)) for i,md,date,theme,count,route,minutes in (
                (1,day1,"2026-10-23","城市初览",1,1,30),(2,day2,"2026-10-24","返程",0,0,0))],
        delivery_status="degraded",terminal_code="workflow_degraded")
    page.route(f"**/api/runs/{RUN}/result",lambda route:route.fulfill(status=200,content_type="application/json",body=json.dumps(data)))
    progress(page,lambda _:packet(settled=True))
    page.goto(origin+"/tasks/"+RUN)
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#task-status")).to_have_text("攻略已完成，部分信息不足")
    expect(page.locator("#task-title")).to_have_text("合肥 · 2日旅行")
    expect(page.locator("#task-dates")).to_have_text("2026-10-23 — 2026-10-24")
    assert not page.locator("#process-overview").evaluate("node=>node.open")
    assert page.locator("#result").bounding_box()["y"]<page.locator("#process-overview").bounding_box()["y"]
    expect(page.locator(".day-reading").first).to_have_text("1 项活动 · 1 段交通 · 日程预留交通 30 分钟")
    expect(page.locator(".transit-details")).to_have_count(1)
    assert not page.locator(".transit-details").evaluate("node=>node.open")
    expect(page.locator(".transit-details summary")).to_contain_text("实时运营未核验")
    expect(page.locator("#itinerary-day-1")).to_contain_text("费用未核验")
    page.locator(".transit-details summary").click()
    expect(page.locator(".transit-details ol")).to_contain_text("在博物馆站下车")
    expect(page.locator("#itinerary-day-1 .source-reference")).to_be_hidden()
    expect(page.locator("#itinerary-day-1")).to_contain_text("营业时间未核验")
    page.locator(".source-details summary").click()
    expect(page.locator("#itinerary-day-1 .source-reference")).to_be_visible()
    assert page.locator("#result-markdown").text_content()==original
    with page.expect_download() as download: page.locator("#download-result").click()
    assert Path(download.value.path()).read_bytes()==original.encode()
    for width,name in ((1440,"guide-desktop"),(390,"guide-mobile")):
        page.set_viewport_size(dict(width=width,height=1000))
        assert page.evaluate("document.documentElement.scrollWidth")<=width
        shoot(page,name)
    page.locator("#result-day-nav a").last.click()
    expect(page.locator("#result-day-nav a").last).to_have_attribute("aria-current","location")
    page.locator("#process-overview > summary").click()
    expect(page.locator("#workflow")).to_be_visible()


def test_b24_12_history_real_titles_page_scoped_filter_error_and_retry(page,origin):
    first=ui_projection(RUN)
    first["trip"]=dict(city="合肥",start_date="2026-10-23",end_date="2026-10-25",days=3)
    second=ui_projection("00000000-0000-4000-8000-000000000125",activity="terminal",lifecycle="closed",
        phase="output_formatting",delivery_status="demo_ready",terminal_code="workflow_completed",finalized_at="2026-10-16T02:00:00Z")
    calls=[];failure=[True]
    def respond(route):
        calls.append(route.request.url)
        if failure[0]: route.fulfill(status=503,content_type="application/json",body=json.dumps(dict(message="服务暂不可用")));return
        value=dict(items=[second] if "cursor=" in route.request.url else [first,second],next_cursor=None if "cursor=" in route.request.url else "next-page")
        route.fulfill(status=200,content_type="application/json",body=json.dumps(value))
    page.route("**/api/runs*",respond)
    page.goto(origin+"/history")
    expect(page.locator("#history-state")).to_contain_text("读取失败")
    page.locator("#history-filter").select_option("failed")
    expect(page.locator(".empty-state")).to_have_count(0)
    page.locator("#history-filter").select_option("all")
    failure[0]=False;page.locator("#history-retry").click()
    expect(page.locator(".history-card")).to_have_count(2)
    expect(page.locator(".history-card h2").first).to_have_text("合肥 · 3日旅行")
    expect(page.locator(".history-card h2").last).to_have_text("旅行规划任务")
    page.locator("#history-filter").select_option("failed")
    expect(page.locator(".empty-state")).to_contain_text("本页没有符合条件")
    expect(page.locator("#history-state")).to_contain_text("筛选仅作用于本页")
    page.locator("#history-filter").select_option("completed")
    expect(page.locator(".history-card")).to_have_count(1)
    failure[0]=True;page.locator("#history-next").click()
    expect(page.locator("#history-retry")).to_be_visible()
    expect(page.locator(".history-card")).to_have_count(1)
    failure[0]=False;page.locator("#history-retry").click()
    expect(page.locator("#history-state")).to_contain_text("已加载本页 1 个任务")
    expect(page.locator("#history-next")).to_be_hidden()
    assert calls[-1].endswith("cursor=next-page") and all("filter=" not in value for value in calls)


@pytest.mark.parametrize("delivery,terminal", [
    ("demo_ready", "workflow_completed"), ("degraded", "workflow_degraded")])
def test_b24_13_delivery_status_waits_for_result_read_and_updates_on_success(page, origin, delivery, terminal):
    view = ui_projection(RUN, activity="terminal", lifecycle="closed", phase="output_formatting",
        delivery_status=delivery, terminal_code=terminal, finalized_at="2026-10-16T02:00:00Z")
    view["result_available"] = True
    route_projection(page, RUN, view)
    attempts = []
    def result(route):
        attempts.append(route.request.url)
        data = dict(message="合成攻略读取失败") if len(attempts) == 1 else dict(
            schema_version="1.0", result_schema_version="2.0", markdown="# 合肥旅行攻略\n\n已整理的完整攻略。",
            days=[], delivery_status=delivery, terminal_code=terminal)
        route.fulfill(status=503 if len(attempts) == 1 else 200,
            content_type="application/json", body=json.dumps(data))
    page.route(f"**/api/runs/{RUN}/result", result)
    page.goto(origin + "/tasks/" + RUN)
    expect(page.locator("#message")).to_have_text("合成攻略读取失败")
    expect(page.locator("#task-status")).to_have_text("正在读取完整攻略")
    expect(page.locator("#result")).to_be_hidden()
    page.locator("#refresh-task").click()
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#task-status")).to_have_text(
        "攻略已完成，部分信息不足" if delivery == "degraded" else "攻略已完成")
    assert len(attempts) == 2


def test_b24_14_delayed_summary_response_cannot_restart_polling_in_background(page, origin):
    held, calls = [], []
    def summary(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json",
                body=json.dumps(dict(schema_version="1.0", status="not_finalized", summary=None)))
    page.route(f"**/api/runs/{RUN}/summary", summary)
    page.clock.install()
    snapshot(page, origin)
    with page.expect_request(f"**/api/runs/{RUN}/summary"):
        page.locator("#statistics > summary").click()
    page.wait_for_timeout(50)
    assert len(held) == 1
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
    held.pop().fulfill(status=200, content_type="application/json",
        body=json.dumps(dict(schema_version="1.0", status="pending_save", summary=None)))
    expect(page.locator("#summary-status")).to_have_text("最终统计正在保存")
    page.clock.fast_forward(10000)
    page.locator("#refresh-summary").click()
    page.clock.fast_forward(10000)
    assert len(calls) == 1
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("#summary-status")).to_have_text("尚未产生最终统计")
    assert len(calls) == 2
