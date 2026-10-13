import json
import os
from pathlib import Path
import time
import pytest
from playwright.sync_api import expect
from app.web_application import WebRunView
from app.requirements import ATTR_TO_PATH


def control(page,origin,**changes):
    response=page.request.post(origin+"/__test/control",data=changes,headers={"Origin":origin})
    assert response.status==200


@pytest.mark.parametrize("code", ["requirement_failed", "workflow_contract_failed"])
def test_closed_input_failure_displays_safe_hub_reason_without_resume_controls(page,origin,code):
    from app.web_application import WebInputIssue
    from app.requirements import validate_requirement_hubs
    from tests.unit.web_application.test_hub_input_details import legacy_state
    state, view = legacy_state(code)
    issues = tuple(WebInputIssue(schema_version="1.0",field=item.field_path,code=item.code,message=item.message)
        for item in validate_requirement_hubs(state.requirement_outcome.requirement))
    projection = WebRunView(schema_version="1.0",run=view,card=None,actions=(),
        result_available=False,summary_status="not_saved",input_issues=issues)
    page.route("**/api/runs/"+str(view.run_id), lambda route: route.fulfill(
        status=200,content_type="application/json",body=projection.model_dump_json()))
    page.goto(origin+"/tasks/"+str(view.run_id))
    expect(page.locator("#task-status")).to_have_text("旅行需求未通过校验")
    expect(page.locator("#input-issues")).to_be_visible()
    expect(page.locator("#input-issues")).to_contain_text("不能只填写城市名")
    expect(page.locator("#input-issues")).to_contain_text("请选择离开枢纽类型")
    assert page.locator("#interaction button, #controls button").count() == 0
    assert "上海" not in page.locator("#input-issues").inner_text()


def info(page,origin): return page.request.get(origin+"/__test/info").json()


def capture_sse(page):
    # Observe actual browser EventSource objects; do not replace their transport.
    page.add_init_script("""(() => {
      const Native = window.EventSource;
      window.ctaTestStreams = [];
      window.ctaTestSnapshots = [];
      window.EventSource = class extends Native {
        constructor(...args) {
          super(...args); window.ctaTestStreams.push(this);
          this.addEventListener('snapshot', event => {
            const value = JSON.parse(event.data);
            if(!value.run)return;
            window.ctaTestSnapshots.push({activity:value.run.activity,phase:value.run.phase,progress_phase:value.progress_phase});
            if(window.ctaTestSnapshots.length>32)window.ctaTestSnapshots.shift();
          });
        }
      };
    })();""")


def fixture_answers(page,origin):
    # Controlled offline travel values; these are not a submitted full_text reply.
    return {ATTR_TO_PATH[name]:json.loads(value) for line in info(page,origin)["full_text"].splitlines()
            for name,separator,value in [line.partition(": ")] if separator}


def answer_current_questions(page,origin,**overrides):
    answers=fixture_answers(page,origin)|overrides
    run_id=page.url.rsplit("/",1)[1]
    questions=page.request.get(origin+f"/api/runs/{run_id}").json()["card"]["questions"]
    for question in questions:
        field=question["field"];value=answers[field];prefix="#clarify-"+field.replace(".","-")
        if question["kind"] in {"single","multiple"}:
            labels=value if isinstance(value,list) else [value]
            if value is None: labels=["由 Agent 决定"]
            for label in labels:
                option=next((item for item in question["options"] if item["label"]==label or item["value"]==label),None)
                if option:
                    page.locator(prefix+"-"+option["value"]).check()
                else:
                    page.locator(prefix+"-other").check();page.locator(prefix+"-other-text").fill(str(label))
        elif question["kind"] == "ages":
            for index,age in enumerate(value): page.locator(f"#clarify-child-age-{index+1}").fill(str(age))
        else:
            page.locator(prefix).fill(str(value).replace(" ","T") if question["kind"]=="datetime" else str(value))
    if "party.children_count" in {question["field"] for question in questions}:
        for index,age in enumerate(answers.get("party.children_ages",[])):
            page.locator(f"#clarify-child-age-{index+1}").fill(str(age))
    return answers


def test_ordinary_http_real_clarify_confirm_hotel_complete_summary_reload_copy_download(page,origin):
    capture_sse(page)
    control(page,origin,phase_blocks=["requirements","hotel_selection","candidate_research","itinerary_planning","output_formatting"])
    page.goto(origin+"/")
    expect(page.locator("#offline-badge")).to_contain_text("离线资格测试")
    assert page.evaluate("isSecureContext") is False
    assert page.evaluate("typeof crypto.getRandomValues") == "function"
    assert page.evaluate("typeof crypto.randomUUID") == "undefined"
    page.locator("#trip-input").fill("我想去杭州")
    page.locator("#date-confirm").check();page.locator("#start-button").dblclick()
    page.wait_for_url("**/tasks/*")
    assert_active_phase(page,origin,"requirements","requirements")
    control(page,origin,phase_release="requirements")
    expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    page.reload();expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    assert info(page,origin)["provider_calls"]==1
    assert page.locator("#interaction input:checked").count()==0
    expect(page.get_by_text("到达杭州的机场/车站", exact=True).first).to_be_visible()
    expect(page.get_by_text("离开杭州的机场/车站", exact=True).first).to_be_visible()
    expect(page.locator("#clarify-time_window-arrival-hub_type-airport")).to_be_visible()
    expect(page.locator("#clarify-time_window-departure-hub_type-railway_station")).to_be_visible()
    answer_current_questions(page,origin,**{"preferences.interests":["人文","自然"]})
    page.get_by_text("其他补充说明（可选）",exact=True).click()
    page.locator("#clarify-input").fill("保留我的选择草稿")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-clarification-options.png"),full_page=True)
    page.wait_for_timeout(10500)
    assert page.locator("#clarify-input").input_value()=="保留我的选择草稿"
    assert page.locator("#clarify-preferences-interests-culture").is_checked()
    assert page.locator("#clarify-preferences-interests-nature").is_checked()
    assert page.locator("#clarify-rooms-room_type_requirements-other-text").count()==0
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    expect(page.locator("#interaction")).to_contain_text("兴趣：人文、自然")
    assert all(label not in page.locator("#interaction").inner_text() for label in ("房型要求", "儿童数量", "行动限制", "饮食禁忌"))
    page.locator("#revision-input").fill("保留草稿：我还在思考")
    assert page.locator("#map-requested").count()==0
    page.wait_for_timeout(10500)
    assert page.locator("#revision-input").input_value()=="保留草稿：我还在思考"
    assert page.locator("#map-requested").count()==0
    with page.expect_request("**/confirm") as confirmation:
        page.get_by_role("button",name="确认需求",exact=True).click()
    assert confirmation.value.post_data_json["map_requested"] is False
    assert_active_phase(page,origin,"hotel_selection","hotel")
    control(page,origin,phase_release="hotel_selection")
    expect(page.get_by_role("button",name="选择这家酒店",exact=True)).to_be_visible()
    expect(page.locator("#task-status")).to_contain_text("选择酒店")
    page.get_by_role("button",name="选择这家酒店",exact=True).click()
    expect(page.locator("#message")).to_contain_text("请先选择")
    page.locator("input[name=hotel]").first.check()
    expect(page.locator(".hotel-card").first).to_have_css("border-color", "rgb(126, 171, 244)")
    expect(page.get_by_role("button",name="全部放弃",exact=True)).to_have_class("danger")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-hotel-options.png"),full_page=True)
    control(page,origin,supplement=True)
    page.get_by_role("button",name="选择这家酒店",exact=True).click()
    assert_active_phase(page,origin,"candidate_research","research")
    control(page,origin,phase_release="candidate_research")
    assert_active_phase(page,origin,"itinerary_planning","planning")
    initial_research_calls=info(page,origin)["fixture_research_calls"]
    control(page,origin,phase_release="itinerary_planning",phase_blocks=["candidate_research"])
    assert_active_phase(page,origin,"candidate_research","research")
    fixture=info(page,origin)
    assert fixture["fixture_supplement_target"]=={"details_status":"not_requested","initial_details_success_count":3}
    assert fixture["fixture_research_calls"]==initial_research_calls
    expect(page.locator('[data-step="planning"]')).to_have_attribute("data-state","pending")
    control(page,origin,phase_release="candidate_research",phase_blocks=["itinerary_planning"])
    assert_active_phase(page,origin,"itinerary_planning","planning")
    assert info(page,origin)["fixture_research_calls"]>initial_research_calls
    control(page,origin,phase_release="itinerary_planning")
    assert_active_phase(page,origin,"output_formatting","formatting")
    control(page,origin,phase_release="output_formatting")
    expect(page.locator("#result")).to_be_visible(timeout=45000)
    assert not page.locator("#statistics").evaluate("e=>e.open")
    run_id=page.url.rsplit("/",1)[1]
    assert page.request.get(origin+f"/api/runs/{run_id}").json()["progress_phase"] is None
    expected=page.request.get(origin+f"/api/runs/{run_id}/result").json()["markdown"]
    assert all(label not in expected for label in ("同行人员", "间房", "- 房型：", "- 行动限制：", "- 饮食禁忌："))
    assert page.locator("#result-markdown").text_content()==expected
    expect(page.locator("#result-days article")).to_have_count(1)
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-guide-main-chain.png"),full_page=True)
    assert "2026-09-02" in expected and "2026-09-04" in expected
    before=info(page,origin)
    page.locator("#statistics summary").click()
    expect(page.locator("#summary-status")).to_contain_text("最终统计已保存",timeout=12000)
    expect(page.locator("#summary-content")).to_contain_text("extraction.extract")
    expect(page.locator("#message")).not_to_contain_text("推送暂时中断")
    page.evaluate("ctaTestStreams[0].onerror(new Event('error'))")
    expect(page.locator("#message")).not_to_contain_text("推送暂时中断")
    page.locator("#copy-result").click()
    expect(page.locator("#message")).to_contain_text("复制")
    page.evaluate("ctaTestStreams[0].onerror(new Event('error'))")
    expect(page.locator("#message")).to_contain_text("复制")
    with page.expect_download() as download:
        page.locator("#download-result").click()
    path=Path(download.value.path());assert path.read_text(encoding="utf-8")==expected
    page.reload();expect(page.locator("#result")).to_be_visible()
    after=info(page,origin);assert (before["provider_calls"],before["map_calls"])==(after["provider_calls"],after["map_calls"])
    cookies=page.context.cookies();assert cookies[0]["httpOnly"] and cookies[0]["sameSite"]=="Lax" and not cookies[0]["secure"]
    assert not page.evaluate("Object.keys(localStorage).length")
    page.goto(origin+"/history");expect(page.get_by_role("link",name="进入任务")).to_have_count(1)
    expect(page.locator(".history-card .task-state")).to_have_text("已完成")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-history.png"),full_page=True)


def assert_active_phase(page,origin,phase,step):
    try:
        expect(page.locator(f'[data-step="{step}"][data-state="running"]')).to_be_visible(timeout=45000)
    except AssertionError:
        fixture=info(page,origin)
        print("fixture_phase_metadata="+json.dumps({key:fixture[key] for key in (
            "fixture_tool_trace","fixture_supplement_target","fixture_research_calls")}))
        raise
    run_id=page.url.rsplit("/",1)[1]
    before=info(page,origin)
    for _ in range(2):
        response=page.request.get(origin+f"/api/runs/{run_id}")
        assert response.status==200
        view=WebRunView.model_validate_json(response.text(),strict=True)
        assert view.run.activity=="executing" and view.run.phase is None
        assert view.progress_phase.value==phase and view.card is None
    page.wait_for_function("phase => {const item=ctaTestSnapshots.at(-1);return item?.activity==='executing' && item.phase===null && item.progress_phase===phase}",arg=phase)
    after=info(page,origin)
    assert (before["provider_calls"],before["map_calls"])==(after["provider_calls"],after["map_calls"])


def test_busy_429_low_disk_preserve_input_samples_and_disconnect_never_restarts(page,origin,browser):
    page.goto(origin+"/");full=info(page,origin)["full_text"]
    control(page,origin,block=True)
    page.locator("#trip-input").fill(full);page.locator("#date-confirm").check();page.locator("#start-button").click();page.wait_for_url("**/tasks/*")
    expect(page.locator("#task-status")).to_contain_text("正在执行")
    expect(page.locator("#task-status")).not_to_contain_text("null")
    run_id=page.url.rsplit("/",1)[1]
    second=page.context.new_page();second.goto(origin+"/");second.locator("#trip-input").fill("杭州：Busy 测试输入仍保留");second.locator("#date-confirm").check();second.locator("#start-button").click()
    expect(second.locator("#message")).to_contain_text("正在执行其他任务")
    assert second.locator("#trip-input").input_value()=="杭州：Busy 测试输入仍保留"
    calls=info(page,origin)["provider_calls"];page.close();control(second,origin,release=True)
    second.goto(origin+f"/tasks/{run_id}");expect(second.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    assert info(second,origin)["provider_calls"]==calls
    second.get_by_role("button",name="取消任务",exact=True).click();expect(second.locator("#task-status")).to_contain_text("已取消",timeout=15000)
    # First scenario used one admission; this one uses one. Fill the remaining
    # three real hourly IP slots via UI and cancel each real checkpointed run.
    remaining=second.request.get(origin+"/api/admission").json()["ip_remaining"]
    for _ in range(remaining):
        second.goto(origin+"/");second.locator("#trip-input").fill(full);second.locator("#date-confirm").check();second.locator("#start-button").click();second.wait_for_url("**/tasks/*")
        expect(second.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
        second.get_by_role("button",name="取消任务",exact=True).click();expect(second.locator("#task-status")).to_contain_text("已取消",timeout=15000)
    second.goto(origin+"/");second.locator("#trip-input").fill("杭州：额度不足也不清空输入");second.locator("#date-confirm").check();second.locator("#start-button").click()
    expect(second.locator("#message")).to_contain_text("体验额度暂时用完")
    assert second.locator("#trip-input").input_value()=="杭州：额度不足也不清空输入"
    control(second,origin,disk=5368709119)
    second.locator("#start-button").click();expect(second.locator("#message")).to_contain_text("存储容量暂不可用")
    assert second.evaluate("sessionStorage.getItem('cta_pending')") is None
    assert second.locator("#trip-input").input_value()=="杭州：额度不足也不清空输入"
    control(second,origin,disk=20*1024**3,advance_seconds=3601)
    before=info(second,origin)["provider_calls"]
    second.locator("#trip-input").fill(full);second.locator("#start-button").click();second.wait_for_url("**/tasks/*")
    expect(second.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    assert info(second,origin)["provider_calls"]==before+1
    second.get_by_role("button",name="取消任务",exact=True).click();expect(second.locator("#task-status")).to_contain_text("已取消",timeout=15000)
    second.goto(origin+"/samples");expect(second.locator("#sample-list")).to_contain_text("真实任务结果")
    expect(second.locator("#sample-list")).to_contain_text("第 3 天")
    second.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-samples.png"),full_page=True)
    second.close()


def test_expired_and_other_browser_uniform_404_xss_canary_and_sse_read_only(page,origin,browser):
    page.goto(origin+"/history");links=page.get_by_role("link",name="进入任务")
    # An empty initial container does not prove the asynchronous session/history
    # bootstrap has finished. Wait for its authenticated empty-history response.
    expect(page.locator("#history-list .empty-state h2")).to_have_text("当前浏览器尚无任务。")
    expect(page.locator("#history-list .history-card")).to_have_count(0)
    expect(page.locator("#history-list .empty-state a")).to_have_attribute("href", "/")
    # This independent cookie has no access to the preceding visitors' tasks.
    expect(links).to_have_count(0)
    response=page.request.get(origin+"/api/runs/00000000-0000-4000-8000-000000000001")
    assert response.status==404 and response.json()["message"]=="任务不存在或不属于当前浏览器"
    page.context.clear_cookies()
    assert page.request.get(origin+"/api/runs").status==401
    page.reload();expect(page.locator("#history-list")).to_contain_text("尚无任务")
    page.goto(origin+"/");canary='<img src=x onerror="globalThis.ctaXss=1"> canary-secret'
    page.locator("#trip-input").fill(canary)
    assert page.evaluate("globalThis.ctaXss") is None
    page.goto(origin+"/samples")
    assert page.evaluate("globalThis.ctaXss") is None
    source=page.request.get(origin+"/static/app.js").text()
    assert "innerHTML" not in source and "Math.random" not in source and "eval(" not in source


def test_truncated_post_response_keeps_original_id_and_queries_one_real_run(page,origin):
    page.goto(origin+"/")
    # Advance the explicitly injected test clock beyond the actual rolling
    # window; neither the fixed five/hour quota nor stored admissions change.
    control(page,origin,advance_seconds=3601)
    before=info(page,origin)["provider_calls"]
    captured=[]
    def truncated(route):
        request=route.request
        captured.append(json.loads(request.post_data)["request_id"])
        response=route.fetch()
        assert response.status==202
        route.fulfill(status=202,headers={"Content-Type":"application/json"},body='{"schema_version":')
    page.route("**/api/runs",truncated)
    page.locator("#trip-input").fill("我想去杭州")
    page.locator("#date-confirm").check();page.locator("#start-button").click()
    expect(page.locator("#query-request")).to_be_visible()
    saved=json.loads(page.evaluate("sessionStorage.getItem('cta_pending')"))
    assert saved["request_id"]==captured[0] and len(captured)==1
    page.unroute("**/api/runs",truncated)
    page.locator("#query-request").click();page.wait_for_url("**/tasks/*")
    expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    assert info(page,origin)["provider_calls"]==before+1
    page.reload();expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    assert info(page,origin)["provider_calls"]==before+1
    cookie=next(item["value"] for item in page.context.cookies() if item["name"]=="cta_visitor")
    (Path(os.environ["MODULE14_EVIDENCE_DIR"])/"sse-shutdown-fixture.json").write_text(json.dumps({"run_id":page.url.rsplit("/",1)[1],"cookie":cookie}),encoding="utf-8")


@pytest.mark.parametrize("terminal_code,expected", [
    ("planning_incomplete", "规划失败，未生成完整攻略"),
    ("workflow_provider_failed", "规划失败，模型服务调用未成功"),
    ("workflow_model_output_invalid", "规划失败，模型输出未通过格式或完整性校验"),
    ("research_no_candidates", "研究失败，地图查询未找到候选地点"),
    ("research_judgments_unfinished", "研究失败，必要候选资格判断尚未完成"),
    ("research_evidence_insufficient", "研究失败，现有证据不足以确认候选用途"),
    ("research_candidates_unsuitable", "研究失败，已核验候选不符合用途或避开要求"),
    ("research_recovery_exhausted", "研究失败，经允许的修正仍未补齐必要判断"),
    ("research_budget_exhausted", "研究失败，研究或模型调用额度已用尽"),
])
def test_registered_failed_terminal_projection_is_clear_without_partial_result(page,origin,terminal_code,expected):
    # A UI projection fixture, not a claim of running a failed business graph.
    run_id="00000000-0000-4000-8000-000000000014"
    view={"schema_version":"1.0","run":{"run_id":run_id,"trace_id":run_id,"created_at":"2026-08-20T00:00:00Z","finalized_at":"2026-08-20T00:01:00Z",
        "lifecycle":"closed","activity":"terminal","phase":"itinerary_planning","delivery_status":"failed","terminal_code":terminal_code,"pause_generation":None,"cancel_requested":False},
        "card":None,"actions":[],"result_available":False,"summary_status":"not_saved"}
    WebRunView.model_validate_json(json.dumps(view),strict=True)
    page.route(f"**/api/runs/{run_id}",lambda route:route.fulfill(status=200,content_type="application/json",body=json.dumps(view)))
    page.goto(origin+f"/tasks/{run_id}")
    expect(page.locator("#task-status")).to_have_text(expected)
    expect(page.locator("#result")).to_be_hidden()
    expect(page.get_by_role("link",name="查看完整样例",exact=True)).to_be_visible()


@pytest.mark.parametrize("other_message", [None, "uncertain_post", "result_error"])
def test_sse_terminal_races_preserve_message_ownership(page,origin,other_message):
    # Explicit UI projection/transport timing fixture, not a completed business
    # graph or supplier qualification. The main-chain test uses the actual graph.
    capture_sse(page)
    run_id="00000000-0000-4000-8000-000000000051"
    view={"schema_version":"1.0","run":{"run_id":run_id,"trace_id":run_id,
        "created_at":"2026-08-20T00:00:00Z","finalized_at":None,"lifecycle":"open",
        "activity":"executing","phase":None,"delivery_status":None,"terminal_code":None,
        "pause_generation":0,"cancel_requested":False},
        "card":None,"actions":["cancel"],"result_available":False,"summary_status":"not_finalized"}
    WebRunView.model_validate_json(json.dumps(view),strict=True)
    page.route(f"**/api/runs/{run_id}",lambda route:route.fulfill(status=200,
        content_type="application/json",body=json.dumps(view)))
    # A native EventSource reaching HTTP EOF while this view remains executing.
    page.route(f"**/api/runs/{run_id}/events",lambda route:route.fulfill(status=200,
        content_type="text/event-stream",body=": finite UI fixture\n\n"))
    posts=[]
    def uncertain(route):
        posts.append(json.loads(route.request.post_data))
        route.fulfill(status=503,content_type="application/json",body=json.dumps({
            "schema_version":"1.0","code":"dependency_unavailable","message":"合成提交结果未知"}))
    page.route(f"**/api/runs/{run_id}/cancel",uncertain)
    page.route(f"**/api/runs/{run_id}/result",lambda route:route.fulfill(status=503,
        content_type="application/json",body=json.dumps({"schema_version":"1.0",
            "code":"dependency_unavailable","message":"合成攻略读取失败"})))
    before=info(page,origin)
    page.goto(origin+f"/tasks/{run_id}")
    expect(page.locator("#message")).to_contain_text("推送暂时中断")
    assert page.evaluate("ctaTestStreams.length") == 1
    pending=None
    if other_message=="uncertain_post":
        page.get_by_role("button",name="取消任务",exact=True).click()
        expect(page.locator("#message")).to_have_text("合成提交结果未知")
        pending=page.evaluate("sessionStorage.getItem('cta_pending')")
        assert pending is not None and len(posts)==1
        page.evaluate("ctaTestStreams[0].onerror(new Event('error'))")
        expect(page.locator("#message")).to_have_text("合成提交结果未知")
    view["run"].update(lifecycle="closed",activity="terminal",phase="output_formatting",
        delivery_status="demo_ready" if other_message=="result_error" else "failed",
        terminal_code="workflow_completed" if other_message=="result_error" else "planning_incomplete",
        finalized_at="2026-08-20T00:01:00Z")
    view.update(actions=[],result_available=other_message=="result_error",summary_status="not_saved")
    WebRunView.model_validate_json(json.dumps(view),strict=True)
    page.evaluate("ctaTestStreams[0].dispatchEvent(new MessageEvent('snapshot', {data:'{}'}))")
    expect(page.locator("#task-status")).to_contain_text("攻略已完成" if other_message=="result_error" else "规划失败")
    expected={None:"", "uncertain_post":"合成提交结果未知", "result_error":"合成攻略读取失败"}[other_message]
    expect(page.locator("#message")).to_have_text(expected)
    # Deliver callbacks after close; no other message may be overwritten and no
    # business request may be retried.
    page.evaluate("ctaTestStreams[0].onerror(new Event('error'))")
    expect(page.locator("#message")).to_have_text(expected)
    assert page.evaluate("ctaTestStreams[0].readyState") == 2
    if pending is not None:
        assert page.evaluate("sessionStorage.getItem('cta_pending')")==pending
        expect(page.locator("#query-request")).to_be_visible()
    after=info(page,origin)
    assert (before["provider_calls"],before["map_calls"])==(after["provider_calls"],after["map_calls"])
    assert len(posts)==(1 if other_message=="uncertain_post" else 0)


def test_new_task_collects_only_active_inputs_and_preserves_agent_decisions(page,origin):
    page.goto(origin+"/");page.locator("#trip-input").fill("我想去杭州")
    page.locator("#date-confirm").check();page.locator("#start-button").click();page.wait_for_url("**/tasks/*")
    expect(page.locator("#clarify-time_window-sightseeing_start_date")).to_be_visible()
    questions=page.request.get(origin+f"/api/runs/{page.url.rsplit('/',1)[1]}").json()["card"]["questions"]
    assert not any(question["field"].startswith(("party.","rooms.","constraints.")) for question in questions)
    answer_current_questions(page,origin,**{"preferences.interests":None,"preferences.pace":None})
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    assert "儿童数量" not in page.locator("#interaction").inner_text()
    assert "房间数量" not in page.locator("#interaction").inner_text()
    assert page.locator("#map-requested").count()==0
    expect(page.locator("#interaction")).to_contain_text("兴趣：由 Agent 决定")
    expect(page.locator("#interaction")).to_contain_text("节奏：由 Agent 决定")
    page.get_by_role("button",name="取消任务",exact=True).click()
    expect(page.locator("#task-status")).to_contain_text("已取消",timeout=15000)


def test_legacy_domain_confirmation_keeps_party_room_fields(page, origin):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    from uuid import UUID
    from app.requirements import RequirementDomainService, RequirementDomainState
    from app.requirements.manual_template import serialize_manual_template
    from app.web_application.presentation import interaction_card
    from app.workflow_contracts import DeliveryStatus, TravelSessionState, WorkflowPhase
    from tests.unit.requirements.fakes import FixedClock, InMemoryEvidenceSink, RecordingRequirementExtractor
    from tests.unit.requirements.test_requirement_service import complete_requirement

    requirement = complete_requirement()
    requirement.party.children_count = 2
    requirement.party.children_ages = [6, 10]
    requirement.rooms.room_count = 2
    requirement.rooms.room_type_requirements = "需要两张床"
    domain = RequirementDomainService(extractor=RecordingRequirementExtractor([]),
        evidence_sink=InMemoryEvidenceSink(), clock=FixedClock(date(2026, 10, 13)))
    transition = domain.process_manual_template(RequirementDomainState(), serialize_manual_template(requirement))
    assert transition.outcome.outcome_type == "need_confirmation"
    run_id = "00000000-0000-4000-8000-000000000078"
    state = TravelSessionState(schema_version="2.0", run_id=UUID(run_id), trace_id=UUID(run_id),
        phase=WorkflowPhase.REQUIREMENTS, delivery_status=DeliveryStatus.AWAITING_USER,
        terminal_code=None, terminal_outcome=None, requirement_state=transition.state,
        requirement_outcome=transition.outcome, artifact_refs=())
    # Playwright 已占用当前线程的事件循环；纯投影在独立循环中完成。
    with ThreadPoolExecutor(max_workers=1) as executor:
        card = executor.submit(lambda: asyncio.run(interaction_card(state=state, repository=None))).result(timeout=10)
    view = {"schema_version":"1.0", "run":{"run_id":run_id,"trace_id":run_id,
        "created_at":"2026-08-20T00:00:00Z","finalized_at":None,"lifecycle":"open",
        "activity":"awaiting_user","phase":"requirements","delivery_status":"awaiting_user",
        "terminal_code":None,"pause_generation":1,"cancel_requested":False},
        "card":card.model_dump(mode="json"), "actions":["confirm"], "result_available":False,
        "summary_status":"not_finalized"}
    WebRunView.model_validate_json(json.dumps(view), strict=True)
    capture_sse(page)
    route_projection(page, run_id, view)
    page.goto(origin + f"/tasks/{run_id}")
    for text in ("儿童数量：2", "儿童年龄：6、10", "房间数量：2", "房型要求：需要两张床"):
        expect(page.locator("#interaction")).to_contain_text(text)
    assert page.locator("#map-requested").count() == 0


def test_controls_fixture_exclusivity_other_age_zero_polling_generation_and_manual_limit(page,origin):
    # A strict DTO/DOM fixture only; the two tests above qualify real M01/M03.
    from app.web_application import WebClarificationQuestion, WebClarificationOption
    from app.requirements import RequirementFieldPath
    def question(field,label,kind,options=(),**extra):
        return WebClarificationQuestion(schema_version="1.0",field=RequirementFieldPath(field),label=label,kind=kind,
            options=tuple(WebClarificationOption(schema_version="1.0",value=value,label=text) for value,text in options),**extra).model_dump(mode="json")
    questions=[question("preferences.interests","兴趣偏好","multiple",(("nature","自然"),("food","美食"),("agent_decides","由 Agent 决定"))),
        question("preferences.pace","行程节奏","single",(("relaxed","轻松"),("agent_decides","由 Agent 决定"))),
        question("rooms.room_type_requirements","房型要求","single",(("twin","双床房"),("other","其他"),("canary",'<img src=x onerror="globalThis.ctaXss=1">'))),
        question("party.children_count","儿童数量","number",minimum=0,maximum=100),
        question("time_window.sightseeing_start_date","正式游玩开始日期","date"),
        question("time_window.arrival.hub_name","到达机场/车站","text"),
        question("time_window.arrival.local_datetime","到达日期与时间","datetime")]
    run_id="00000000-0000-4000-8000-000000000020"
    view={"schema_version":"1.0","run":{"run_id":run_id,"trace_id":run_id,"created_at":"2026-08-20T00:00:00Z","finalized_at":None,
        "lifecycle":"open","activity":"awaiting_user","phase":"requirements","delivery_status":"awaiting_user","terminal_code":None,"pause_generation":1,"cancel_requested":False},
        "card":{"schema_version":"1.0","kind":"clarification","title":"补充旅行需求（页面控件测试）","paragraphs":["保留原领域问题"],"facts":[],"choices":[],"questions":questions},
        "actions":["clarify"],"result_available":False,"summary_status":"not_finalized"}
    WebRunView.model_validate_json(json.dumps(view),strict=True)
    page.route(f"**/api/runs/{run_id}",lambda route:route.fulfill(status=200,content_type="application/json",body=json.dumps(view)))
    submitted=[]
    def receipt(route):
        payload=json.loads(route.request.post_data);submitted.append(payload)
        route.fulfill(status=202,content_type="application/json",body=json.dumps(dict(schema_version="1.0",request_id=payload["request_id"],run_id=run_id,status="dispatched",reason=None)))
    page.route(f"**/api/runs/{run_id}/clarify",receipt)
    page.goto(origin+f"/tasks/{run_id}")
    expect(page.locator("#clarify-party-children_count")).to_be_visible()
    assert page.evaluate("globalThis.ctaXss") is None and page.locator("#interaction img").count()==0
    assert page.locator("#interaction input:checked").count()==0
    assert page.locator("#clarify-party-children_count").input_value()==""
    page.get_by_role("button",name="提交补充",exact=True).click();assert not submitted
    page.locator("#clarify-party-children_count").fill("2")
    page.locator("#clarify-child-age-1").fill("6");page.locator("#clarify-child-age-2").fill("10")
    page.locator("#clarify-preferences-interests-nature").check();page.locator("#clarify-preferences-interests-agent_decides").check()
    assert not page.locator("#clarify-preferences-interests-nature").is_checked()
    page.locator("#clarify-preferences-interests-food").check()
    assert not page.locator("#clarify-preferences-interests-agent_decides").is_checked()
    page.locator("#clarify-preferences-pace-agent_decides").check()
    page.locator("#clarify-time_window-sightseeing_start_date").fill("2026-09-03")
    page.locator("#clarify-time_window-arrival-hub_name").fill("上海浦东机场T2航站楼")
    page.locator("#clarify-time_window-arrival-local_datetime").fill("2026-09-02T18:00")
    page.locator("#clarify-rooms-room_type_requirements-other").check()
    page.get_by_role("button",name="提交补充",exact=True).click();assert not submitted
    page.locator("#clarify-rooms-room_type_requirements-other-text").fill("安静的房间")
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-clarification-mobile.png"),full_page=True)
    page.wait_for_timeout(10500)
    assert page.locator("#clarify-child-age-2").input_value()=="10"
    assert page.locator("#clarify-rooms-room_type_requirements-other-text").input_value()=="安静的房间"
    assert page.locator("#clarify-preferences-interests-food").is_checked()
    page.locator("#clarify-party-children_count").fill("0")
    assert page.locator("#clarify-child-age-1").count()==0
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.locator("#message")).to_contain_text("操作已接纳")
    assert len(submitted)==1 and "儿童数量：0" in submitted[0]["user_message"] and "儿童年龄" not in submitted[0]["user_message"]
    assert "行程节奏：由 Agent 决定" in submitted[0]["user_message"]
    assert "到达机场/车站：上海浦东机场T2航站楼" in submitted[0]["user_message"]
    assert "到达日期与时间：2026-09-02 18:00" in submitted[0]["user_message"]
    view["run"]["pause_generation"]=2
    page.wait_for_timeout(10500)
    assert page.locator("#interaction input:checked").count()==0
    assert page.locator("#clarify-party-children_count").input_value()==""
    page.get_by_text("其他补充说明（可选）",exact=True).click();page.locator("#clarify-manual-mode").check()
    page.locator("#clarify-input").fill("字"*8001)
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.locator("#message")).to_contain_text("8000字");assert len(submitted)==1
    page.locator("#clarify-input").fill("本轮用文字回答，保留原领域处理。")
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.locator("#message")).to_contain_text("操作已接纳")
    assert len(submitted)==2 and submitted[1]["pause_generation"]==2
    assert submitted[1]["user_message"]=="本轮用文字回答，保留原领域处理。"
    assert page.evaluate("Object.keys(localStorage).length") == 0


# UI projection fixtures below test the presentation of public DTOs only.
# The existing main-chain test above still uses the actual M01/M03/M11/M13/M12 and PostgreSQL.
def ui_projection(run_id, **run_fields):
    view = {"schema_version": "1.0", "run": {
        "run_id": run_id, "trace_id": run_id, "created_at": "2026-08-20T00:00:00Z",
        "finalized_at": None, "lifecycle": "open", "activity": "executing",
        "phase": "candidate_research", "delivery_status": "in_progress",
        "terminal_code": None, "pause_generation": None, "cancel_requested": False,
    } | run_fields, "card": None, "actions": [], "result_available": False,
        "summary_status": "not_finalized"}
    if view["run"]["activity"] == "executing":
        view["progress_phase"], view["run"]["phase"] = view["run"]["phase"], None
    WebRunView.model_validate_json(json.dumps(view), strict=True)
    return view


def route_projection(page, run_id, view):
    def respond(route):
        projection = WebRunView.model_validate_json(json.dumps(view), strict=True)
        route.fulfill(status=200, content_type="application/json", body=projection.model_dump_json())
    page.route(f"**/api/runs/{run_id}", respond)


def refresh_projection(page):
    # Dispatch a public snapshot notification; the page then really reads the routed HTTP DTO.
    page.evaluate("ctaTestStreams[0].dispatchEvent(new MessageEvent('snapshot', {data:'{}'}))")


def test_workflow_canvas_tracks_public_phases_research_return_and_reduced_motion(page, origin):
    capture_sse(page)
    run_id = "00000000-0000-4000-8000-000000000061"
    view = ui_projection(run_id)
    route_projection(page, run_id, view)
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
    page.set_viewport_size({"width": 1280, "height": 920})
    page.goto(origin + f"/tasks/{run_id}")
    expect(page.locator('[data-step="research"][data-state="running"]')).to_be_visible()
    page.wait_for_function("() => ctaTestStreams.length === 1")
    expect(page.locator(".flow-node")).to_have_count(6)
    assert "确认并补充" not in page.locator("#workflow").inner_text()
    assert "%" not in page.locator("#workflow").inner_text()
    widths = page.locator(".flow-node").evaluate_all("nodes=>nodes.map(node=>node.getBoundingClientRect().width)")
    assert max(widths) - min(widths) < 1
    assert page.locator(".flow-node").evaluate_all("nodes=>nodes.every(node=>Math.abs(node.getBoundingClientRect().height-88)<1)")
    assert page.locator(".flow-node").evaluate_all("""nodes=>nodes.every(node=>{
        const a=node.getBoundingClientRect(),b=node.parentElement.getBoundingClientRect();
        return Math.abs(a.left+a.width/2-b.left-b.width/2)<1;
    })""")
    expect(page.locator("#workflow-paths path")).to_have_count(6)
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / "browser-flow-desktop.png"), full_page=True)
    for phase, step in [
        ("requirements", "requirements"), ("hotel_selection", "hotel"),
        ("candidate_research", "research"), ("itinerary_planning", "planning"),
        ("validation", "planning"), ("budget_evaluation", "planning"),
        ("output_formatting", "formatting"), ("itinerary_planning", "planning"),
        ("candidate_research", "research"),
    ]:
        view["progress_phase"] = phase
        refresh_projection(page)
        expect(page.locator(f'[data-step="{step}"][data-state="running"]')).to_be_visible()
        expect(page.locator('.flow-node[data-state="running"]')).to_have_count(1)
        expect(page.locator(f'[data-step="{step}"]')).to_have_attribute("aria-current", "step")
    expect(page.locator('[data-step="planning"]')).to_have_attribute("data-state", "pending")
    page.emulate_media(reduced_motion="reduce")
    expect(page.locator('[data-step="research"]')).to_have_css("animation-name", "none")
    expect(page.locator('[data-step="research"] .flow-indicator')).to_have_css("animation-name", "none")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("#workflow-edges")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / "browser-flow-mobile.png"), full_page=True)
    view["progress_phase"] = None
    refresh_projection(page)
    expect(page.locator('.flow-node[data-state="running"]')).to_have_count(0)
    expect(page.locator('.flow-node[data-state="complete"]')).to_have_count(2)
    expect(page.locator('[data-step="research"]')).to_have_attribute("data-state", "observed")
    expect(page.locator("#phase-title")).to_have_text("当前阶段暂不可读")
    assert not writes


def test_workflow_wait_recovery_blocked_cancel_and_failed_never_animate_as_running(page, origin):
    capture_sse(page)
    run_id = "00000000-0000-4000-8000-000000000062"
    view = ui_projection(run_id, phase="itinerary_planning")
    route_projection(page, run_id, view)
    page.goto(origin + f"/tasks/{run_id}")
    page.wait_for_function("() => ctaTestStreams.length === 1")
    for activity, lifecycle, expected, label in [
        ("awaiting_user", "open", "waiting", "等待你的选择"),
        ("recovery_required", "open", "stopped", "需要明确恢复"),
        ("blocked", "blocked", "stopped", "当前任务无法安全继续"),
        ("incompatible", "blocked", "stopped", "任务版本不兼容"),
        ("cancelling", "open", "waiting", "正在取消"),
    ]:
        view["progress_phase"] = None
        view["run"]["phase"] = "itinerary_planning"
        view["run"].update(activity=activity, lifecycle=lifecycle, cancel_requested=activity == "cancelling")
        refresh_projection(page)
        expect(page.locator('[data-step="planning"]')).to_have_attribute("data-state", expected)
        expect(page.locator("#phase-badge")).to_have_text(label)
        expect(page.locator('.flow-node[data-state="running"]')).to_have_count(0)
        expect(page.locator('[data-step="planning"] .flow-indicator')).to_have_css("animation-name", "none")
        expect(page.locator('[data-step="delivered"]')).to_have_attribute("data-state", "pending")
        if activity == "awaiting_user":
            expect(page.locator("#phase-description")).to_contain_text("暂时没有可用的选项")
    view["run"].update(activity="terminal", lifecycle="closed", delivery_status="cancelled",
                       terminal_code="cancelled_by_user", finalized_at="2026-08-20T00:01:00Z")
    refresh_projection(page)
    expect(page.locator("#task-status")).to_have_text("已取消")
    expect(page.locator("#phase-description")).to_contain_text("当前没有可交付的完整攻略")
    expect(page.locator('[data-step="planning"]')).to_have_attribute("data-state", "stopped")
    expect(page.locator('[data-step="planning"] .flow-node-status')).to_have_text("已停止")
    expect(page.locator("#result")).to_be_hidden()
    view["run"].update(delivery_status="failed", terminal_code="workflow_provider_failed", cancel_requested=False)
    page.reload()
    expect(page.locator("#task-status")).to_contain_text("规划失败")
    expect(page.locator('[data-step="delivered"]')).to_have_attribute("data-state", "pending")
    expect(page.locator('.flow-node[data-state="running"]')).to_have_count(0)


def test_workflow_failed_read_retains_last_observation_stops_motion_then_recovers(page, origin):
    capture_sse(page)
    run_id = "00000000-0000-4000-8000-000000000063"
    view = ui_projection(run_id)
    outage = [False]
    def respond(route):
        if outage[0]:
            route.fulfill(status=503, content_type="application/json",
                          body=json.dumps({"schema_version": "1.0", "code": "dependency_unavailable",
                                           "message": "服务暂不可用，请稍后查询",
                                           "request_id": None, "retry_after_seconds": None}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(view))
    page.route(f"**/api/runs/{run_id}", respond)
    page.goto(origin + f"/tasks/{run_id}")
    page.wait_for_function("() => ctaTestStreams.length === 1")
    expect(page.locator('[data-step="research"]')).to_have_attribute("data-state", "running")
    outage[0] = True
    refresh_projection(page)
    expect(page.locator("#workflow")).to_have_attribute("data-connection", "unavailable")
    expect(page.locator("#phase-description")).to_contain_text("最近一次确认的阶段")
    expect(page.locator('[data-step="research"]')).to_have_css("animation-name", "none")
    expect(page.locator('[data-step="research"] .flow-indicator')).to_have_css("animation-name", "none")
    outage[0] = False
    view["progress_phase"] = "itinerary_planning"
    refresh_projection(page)
    expect(page.locator("#workflow")).to_have_attribute("data-connection", "available")
    expect(page.locator('[data-step="planning"]')).to_have_attribute("data-state", "running")


def test_result_readable_overview_days_transit_sources_safe_dom_and_original_download(page, origin):
    from app.web_application import WebResultView
    run_id = "00000000-0000-4000-8000-000000000064"
    view = ui_projection(run_id, activity="terminal", lifecycle="closed", phase="output_formatting",
                         delivery_status="degraded", terminal_code="workflow_degraded",
                         finalized_at="2026-08-20T00:01:00Z")
    view["result_available"] = True
    route_projection(page, run_id, view)
    header = "# 杭州旅行攻略\n\n- 游览日期：2026-09-03—2026-09-04；共 2 天。\n\n## 已确认酒店\n\n- 西湖酒店\\#1\\/A；地址：湖滨路 2\\/3 号。\n- 房态与价格未核验。"
    day1 = ("## 第 1 天｜2026-09-03｜西湖\\#周边\n\n- 天气未获取。\n\n### 主行程\n\n"
            "- 2026-09-03 09:00—2026-09-03 09:30（北京时间）｜交通：酒店 → 西湖；公共交通；费用未核验。〔来源 1〕\n\n"
            "  乘车顺序（班次与实时运营未核验）：\n\n  1. 在湖滨站乘车。\n  2. 在西湖站下车。\n\n"
            "- 2026-09-03 10:00—2026-09-03 11:00（北京时间）｜景点：湖边；地点身份已核验〔来源 1〕；地址：湖滨路；营业时间未核验，请出行前确认。\n\n"
            "- 未匹配编号〔来源 99〕；代码：\x60〔来源 1〕\x60；[保留链接〔来源 1〕](https://example.com/reference)。\n\n"
            "| 时间 | 安排 |\n| --- | --- |\n| 12:00 | 午餐\\|备选 |\n\n"
            "- **保留提醒**；路径 C:\\\\旅行\\/计划。\n\n"
            "&lt;img src=x onerror=&quot;globalThis.ctaXss=1&quot;&gt;\n\n"
            "[危险链接](javascript:alert(1))；[地点来源](https://example.com/place)\n")
    day2 = "## 第 2 天｜2026-09-04｜返程\n\n- 日类型：纯交通日。\n- 本日未安排游览。"
    footer = "## 酒店到返程枢纽\n\n- 返程：2026-09-04 18:00；请预留交通时间。\n\n## 费用核验状态\n\n- 住宿：未核验。\n\n## 来源清单\n\n- 〔来源 1〕百度地图｜获取时间：2026-08-20；查询时快照。\n\n## 出行前复核\n\n- 部分营业信息不足，出行前请复核。"
    # Match M12's exact assembly, including each day fragment's original bytes.
    day1 = day1.rstrip("\n")
    original = header + "\n\n" + day1 + "\n\n" + day2 + "\n\n" + footer + "\n"
    result = WebResultView.model_validate_json(json.dumps({
        "schema_version": "1.0", "result_schema_version": "2.0", "markdown": original,
        "days": [{"schema_version": "1.0", "ordinal": 1, "markdown": day1},
                 {"schema_version": "1.0", "ordinal": 2, "markdown": day2}],
        "delivery_status": "degraded", "terminal_code": "workflow_degraded",
    }), strict=True)
    page.route(f"**/api/runs/{run_id}/result", lambda route: route.fulfill(
        status=200, content_type="application/json", body=result.model_dump_json()))
    page.set_viewport_size({"width": 1280, "height": 920})
    page.goto(origin + f"/tasks/{run_id}")
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator('.flow-node[data-state="complete"]')).to_have_count(6)
    expect(page.locator("#delivery-note")).to_contain_text("部分信息不足")
    expect(page.locator("#result-overview")).to_contain_text("西湖酒店#1/A")
    expect(page.locator("#result-overview")).to_contain_text("2/3 号")
    expect(page.locator("#result-days article")).to_have_count(2)
    expect(page.locator("#result-day-nav a")).to_have_count(2)
    expect(page.locator("#itinerary-day-1 .timeline-time")).to_have_count(2)
    expect(page.locator("#itinerary-day-1 ol")).to_contain_text("在西湖站下车")
    expect(page.locator("#itinerary-day-1 td")).to_contain_text(["12:00", "午餐|备选"])
    expect(page.locator("#itinerary-day-1 strong")).to_have_text("保留提醒")
    expect(page.locator("#itinerary-day-2")).to_contain_text("本日未安排游览")
    expect(page.locator("#result-notes")).to_contain_text("返程：2026-09-04 18:00")
    expect(page.locator("#result-notes")).to_contain_text("〔来源 1〕")
    expect(page.locator("#result-notes")).to_contain_text("部分营业信息不足")
    expect(page.locator("#result-notes .source-details")).to_have_count(1)
    assert not page.locator(".source-details").evaluate("node=>node.open")
    assert "地点身份已核验" not in page.locator("#itinerary-day-1").inner_text()
    expect(page.locator("#itinerary-day-1 .source-reference").first).to_be_hidden()
    expect(page.locator("#itinerary-day-1")).to_contain_text("营业时间未核验，请出行前确认")
    expect(page.locator("#itinerary-day-1")).to_contain_text("地址：湖滨路")
    expect(page.locator("#itinerary-day-1 code")).to_have_text("〔来源 1〕")
    expect(page.get_by_role("link", name="保留链接〔来源 1〕", exact=True)).to_be_visible()
    assert "〔来源 99〕" in page.locator("#itinerary-day-1").inner_text()
    page.locator(".source-details summary").click()
    expect(page.locator("#itinerary-day-1 .source-reference").first).to_be_visible()
    assert "地点身份已核验〔来源 1〕" in page.locator("#itinerary-day-1").inner_text()
    expect(page.locator(".source-content")).to_contain_text("获取时间：2026-08-20")
    page.locator(".source-details summary").click()
    expect(page.locator("#itinerary-day-1 .source-reference").first).to_be_hidden()
    assert page.locator("#result img, #result script").count() == 0
    assert page.evaluate("globalThis.ctaXss") is None
    assert page.locator('#result a[href^="javascript:"]').count() == 0
    expect(page.get_by_role("link", name="地点来源", exact=True)).to_have_attribute("rel", "noopener noreferrer")
    assert not page.locator(".raw-result").evaluate("node=>node.open")
    assert page.locator("#result-markdown").text_content() == original
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / "browser-guide-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / "browser-guide-mobile.png"), full_page=True)
    with page.expect_download() as download:
        page.locator("#download-result").click()
    assert Path(download.value.path()).read_text(encoding="utf-8") == original
    expect(page.locator("#statistics")).not_to_have_attribute("open", "")


def test_result_delivery_only_completes_after_successful_read_and_has_lossless_fallback(page, origin):
    run_id = "00000000-0000-4000-8000-000000000065"
    view = ui_projection(run_id, activity="terminal", lifecycle="closed", phase="output_formatting",
                         delivery_status="demo_ready", terminal_code="workflow_completed",
                         finalized_at="2026-08-20T00:01:00Z")
    view["result_available"] = True
    route_projection(page, run_id, view)
    available = [False]
    original = "# 完整攻略\n\n总览与全部提醒保留。费用未核验。〔来源 1〕\n\n## 来源清单\n\n- 〔来源 1〕百度地图｜获取时间：2026-08-20。\n"
    def result(route):
        if not available[0]:
            route.fulfill(status=503, content_type="application/json",
                          body=json.dumps({"schema_version": "1.0", "code": "dependency_unavailable",
                                           "message": "服务暂不可用，请稍后查询",
                                           "request_id": None, "retry_after_seconds": None}))
        else:
            # A deliberately mismatched DOM fixture, not a valid M12 business Artifact.
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "schema_version": "1.0", "result_schema_version": "2.0", "markdown": original,
                "days": [{"schema_version": "1.0", "ordinal": 1, "markdown": "不匹配的日片段"}],
                "delivery_status": "demo_ready", "terminal_code": "workflow_completed",
            }))
    page.route(f"**/api/runs/{run_id}/result", result)
    page.goto(origin + f"/tasks/{run_id}")
    expect(page.locator("#workflow")).to_have_attribute("data-connection", "unavailable")
    expect(page.locator('[data-step="delivered"]')).to_have_attribute("data-state", "waiting")
    expect(page.locator('.flow-node[data-state="complete"]')).to_have_count(5)
    expect(page.locator("#result")).to_be_hidden()
    available[0] = True
    expect(page.locator("#result")).to_be_visible(timeout=15000)
    expect(page.locator('.flow-node[data-state="complete"]')).to_have_count(6)
    expect(page.locator("#result-overview")).to_contain_text("总览与全部提醒保留")
    expect(page.locator("#result-day-nav")).to_be_hidden()
    expect(page.locator("#result-days article")).to_have_count(0)
    expect(page.locator("#result-overview")).to_contain_text("〔来源 1〕")
    expect(page.locator(".source-details")).to_have_count(0)
    assert page.locator("#result-markdown").text_content() == original


@pytest.mark.parametrize("path,page_name", [("/", "home"), ("/history", "history"), ("/samples", "samples")])
def test_shared_page_style_navigation_and_mobile_layout(page, origin, path, page_name):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(origin + path)
    expect(page.locator("body")).to_have_attribute("data-page", page_name)
    expect(page.locator(".site-header nav a")).to_have_count(3)
    expect(page.locator('.site-header nav a[aria-current="page"]')).to_have_count(1)
    if page_name == "history":
        expect(page.locator(".empty-state")).to_be_visible()
    if page_name == "samples":
        expect(page.locator(".sample-card").first).to_be_visible()
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()") == "#3774d7"
    expect(page.locator(".button, button").first).to_have_css("border-radius", "8px")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / f"browser-{page_name}-unified-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.locator(".site-header nav a").first.focus()
    expect(page.locator(".site-header nav a").first).to_be_focused()
    expect(page.locator(".site-header nav a").first).to_have_css("outline-style", "solid")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"]) / f"browser-{page_name}-unified-mobile.png"), full_page=True)
    if page_name == "history":
        expect(page.locator(".empty-state")).to_contain_text("当前浏览器尚无任务")
        expect(page.locator(".empty-state a")).to_have_attribute("href", "/")
    if page_name == "samples":
        expect(page.locator(".sample-meta").first).to_contain_text("真实任务结果")
        expect(page.locator(".sample-meta").first).to_contain_text("非实时")


@pytest.mark.parametrize("suffix", [
    "## 来源清单\n\n- 来源说明格式不同，保留整段内容。",
    "## 来源清单\n\n- 〔来源 1〕百度地图｜查询时快照。\n\n## 来源清单\n\n- 〔来源 2〕百度地图｜另一条查询。",
    "## 来源清单\n\n- 〔来源 1〕百度地图｜查询时快照。\n\n具体交通提醒不能收进来源列表。",
])
def test_unrecognized_source_directory_keeps_all_guide_content_visible(page, origin, suffix):
    run_id = "00000000-0000-4000-8000-000000000067"
    view = ui_projection(run_id, activity="terminal", lifecycle="closed", phase="output_formatting",
                         delivery_status="demo_ready", terminal_code="workflow_completed",
                         finalized_at="2026-08-20T00:01:00Z")
    view["result_available"] = True
    route_projection(page, run_id, view)
    day = "## 第 1 天\n\n### 主行程\n\n- 景点：湖边；地点身份已核验〔来源 1〕；营业时间未核验。"
    original = "# 攻略\n\n" + day + "\n\n" + suffix + "\n"
    page.route(f"**/api/runs/{run_id}/result", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "schema_version": "1.0", "result_schema_version": "2.0", "markdown": original,
            "days": [{"schema_version": "1.0", "ordinal": 1, "markdown": day}],
            "delivery_status": "demo_ready", "terminal_code": "workflow_completed",
        })))
    page.goto(origin + f"/tasks/{run_id}")
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator(".source-details")).to_have_count(0)
    assert "地点身份已核验〔来源 1〕" in page.locator("#result-days").inner_text()
    assert page.locator("#result-markdown").text_content() == original
    assert page.locator("#result-notes").inner_text().strip()


def test_sample_guide_uses_source_disclosure_and_preserves_synthetic_label(page, origin):
    samples = page.request.get(origin + "/api/samples").json()
    samples["items"][0]["source_kind"] = "synthetic"
    samples["items"][0]["markdown"] = (
        "# 合成示例\n\n- 景点：湖边；地点身份已核验〔来源 1〕；营业时间未核验。\n\n"
        "## 来源清单\n\n- 〔来源 1〕百度地图｜合成来源格式示例。\n\n"
        "## 出行前复核\n\n费用未核验。")
    page.route("**/api/samples", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(samples)))
    page.goto(origin + "/samples")
    expect(page.locator(".sample-meta").first).to_contain_text("离线合成模拟样例")
    expect(page.locator(".sample-body .source-reference").first).to_be_hidden()
    assert not page.locator(".source-details").evaluate("node=>node.open")
    expect(page.locator(".sample-body").first).to_contain_text("营业时间未核验")
    expect(page.locator(".sample-body").first).to_contain_text("费用未核验")
    page.locator(".source-details summary").click()
    expect(page.locator(".sample-body .source-reference").first).to_be_visible()
