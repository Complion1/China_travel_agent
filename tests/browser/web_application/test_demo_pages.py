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
      window.EventSource = class extends Native {
        constructor(...args) { super(...args); window.ctaTestStreams.push(this); }
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
    page.goto(origin+"/")
    expect(page.locator("#offline-badge")).to_contain_text("离线资格测试")
    assert page.evaluate("isSecureContext") is False
    assert page.evaluate("typeof crypto.getRandomValues") == "function"
    assert page.evaluate("typeof crypto.randomUUID") == "undefined"
    page.locator("#trip-input").fill("我想去杭州")
    page.locator("#date-confirm").check();page.locator("#start-button").dblclick()
    page.wait_for_url("**/tasks/*")
    expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    page.reload();expect(page.locator("#clarify-preferences-interests-culture")).to_be_visible()
    assert info(page,origin)["provider_calls"]==1
    assert page.locator("#interaction input:checked").count()==0
    expect(page.get_by_text("到达杭州的机场/车站", exact=True).first).to_be_visible()
    expect(page.get_by_text("离开杭州的机场/车站", exact=True).first).to_be_visible()
    expect(page.locator("#clarify-time_window-arrival-hub_type-airport")).to_be_visible()
    expect(page.locator("#clarify-time_window-departure-hub_type-railway_station")).to_be_visible()
    answer_current_questions(page,origin,**{"preferences.interests":["人文","自然"],"rooms.room_type_requirements":"需要两张床"})
    page.get_by_text("其他补充说明（可选）",exact=True).click()
    page.locator("#clarify-input").fill("保留我的选择草稿")
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-clarification-options.png"),full_page=True)
    page.wait_for_timeout(10500)
    assert page.locator("#clarify-input").input_value()=="保留我的选择草稿"
    assert page.locator("#clarify-preferences-interests-culture").is_checked()
    assert page.locator("#clarify-preferences-interests-nature").is_checked()
    assert page.locator("#clarify-rooms-room_type_requirements-other-text").input_value()=="需要两张床"
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    expect(page.locator("#interaction")).to_contain_text("兴趣：人文、自然")
    expect(page.locator("#interaction")).to_contain_text("房型要求：需要两张床")
    page.locator("#revision-input").fill("保留草稿：我还在思考")
    page.locator("#map-requested").check()
    page.wait_for_timeout(10500)
    assert page.locator("#revision-input").input_value()=="保留草稿：我还在思考"
    assert page.locator("#map-requested").is_checked()
    page.locator("#map-requested").uncheck()
    page.get_by_role("button",name="确认需求",exact=True).click()
    expect(page.get_by_role("button",name="选择这家酒店",exact=True)).to_be_visible()
    expect(page.locator("#task-status")).to_contain_text("选择酒店")
    page.get_by_role("button",name="选择这家酒店",exact=True).click()
    expect(page.locator("#message")).to_contain_text("请先选择")
    page.locator("input[name=hotel]").first.check()
    page.get_by_role("button",name="选择这家酒店",exact=True).click()
    expect(page.locator("#result")).to_be_visible(timeout=45000)
    assert not page.locator("#statistics").evaluate("e=>e.open")
    run_id=page.url.rsplit("/",1)[1]
    expected=page.request.get(origin+f"/api/runs/{run_id}/result").json()["markdown"]
    assert page.locator("#result-markdown").text_content()==expected
    expect(page.locator("#result-days article")).to_have_count(1)
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
    page.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-history.png"),full_page=True)


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
    second.goto(origin+"/samples");expect(second.locator("#sample-list")).to_contain_text("离线合成模拟样例")
    expect(second.locator("#sample-list")).to_contain_text("游览日三")
    second.screenshot(path=str(Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-samples.png"),full_page=True)
    second.close()


def test_expired_and_other_browser_uniform_404_xss_canary_and_sse_read_only(page,origin,browser):
    page.goto(origin+"/history");links=page.get_by_role("link",name="进入任务")
    # An empty initial container does not prove the asynchronous session/history
    # bootstrap has finished. Wait for its authenticated empty-history response.
    expect(page.locator("#history-list")).to_have_text("当前浏览器尚无任务。")
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
    ("workflow_provider_failed", "规划失败，模型调用或输出校验未通过"),
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


def test_real_children_and_agent_decisions_are_extracted_without_filling_absent_fields(page,origin):
    page.goto(origin+"/");page.locator("#trip-input").fill("我想去杭州")
    page.locator("#date-confirm").check();page.locator("#start-button").click();page.wait_for_url("**/tasks/*")
    expect(page.locator("#clarify-party-children_count")).to_be_visible()
    answer_current_questions(page,origin,**{"party.children_count":2,"party.children_ages":[6,10],
        "rooms.room_count":2,"preferences.interests":None,"preferences.pace":None})
    assert page.locator("#clarify-child-age-1").input_value()=="6"
    assert page.locator("#clarify-child-age-2").input_value()=="10"
    page.get_by_role("button",name="提交补充",exact=True).click()
    expect(page.get_by_role("button",name="确认需求",exact=True)).to_be_visible()
    expect(page.locator("#interaction")).to_contain_text("儿童数量：2")
    expect(page.locator("#interaction")).to_contain_text("儿童年龄：6、10")
    expect(page.locator("#interaction")).to_contain_text("兴趣：由 Agent 决定")
    expect(page.locator("#interaction")).to_contain_text("节奏：由 Agent 决定")
    page.get_by_role("button",name="取消任务",exact=True).click()
    expect(page.locator("#task-status")).to_contain_text("已取消",timeout=15000)


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
