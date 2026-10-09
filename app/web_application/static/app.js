"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const make = (tag, text, parent) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(parent)parent.append(e); return e; };
  let csrf=null, inflight=false, pending=null, current=null, markdown=null, stream=null, summaryTimer=null, summaryAttempts=0, interactionKey=null;
  let messageSource=null;
  const message=(text,source=null)=>{if($("message"))$("message").textContent=text;messageSource=source;};
  const remember=(requestId,runId)=>{pending={request_id:requestId,run_id:runId||null}; try{sessionStorage.setItem("cta_pending",JSON.stringify(pending));}catch(e){} };
  const clearPending=()=>{pending=null;try{sessionStorage.removeItem("cta_pending");}catch(e){} if($("query-request"))$("query-request").hidden=true;};
  function requestId(){
    if(!globalThis.crypto || typeof crypto.getRandomValues!=="function")throw new Error("此浏览器不支持安全提交，请更换浏览器。");
    const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
    const h=Array.from(b,v=>v.toString(16).padStart(2,"0")).join("");
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  }
  async function api(path,body){
    const options={credentials:"same-origin",cache:"no-store"};
    if(body){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf};options.body=JSON.stringify(body);}
    let response;try{response=await fetch(path,options);}catch(e){const error=new Error("网络中断，请查询原请求；任务会继续执行。");error.uncertain=Boolean(body);throw error;}
    let data;try{data=await response.json();}catch(e){const error=new Error("响应未完整收到，请查询原请求；任务可能已被接纳。");error.uncertain=Boolean(body);throw error;}
    if(!response.ok){const error=new Error(data.message||"服务暂不可用，请稍后刷新。");error.code=data.code;const rejected=data.schema_version==="1.0"&&["capacity_unavailable","service_closing"].includes(data.code);error.uncertain=Boolean(body)&&(response.status>=500)&&!rejected;error.status=response.status;throw error;}
    return data;
  }
  async function session(){const data=await api("/api/session");csrf=data.csrf_token;}
  const phases={requirements:"确认需求",hotel_selection:"选择酒店",candidate_research:"查找候选",itinerary_planning:"规划行程",validation:"核验行程",budget_evaluation:"检查行程预算",output_formatting:"整理攻略"};
  const activities={idle:"准备中",executing:"正在执行",awaiting_user:"等待你的选择",recovery_required:"需要明确恢复",blocked:"当前任务无法安全继续",incompatible:"任务版本不兼容",terminal:"已结束"};
  const terminal={workflow_completed:"攻略已完成",workflow_degraded:"攻略已完成，部分信息不足",cancelled_by_user:"已取消",hotel_declined:"已放弃酒店选择",workflow_provider_failed:"规划失败，模型调用或输出校验未通过",requirement_failed:"旅行需求未通过校验"};
  function inputIssues(view){
    const parent=$("input-issues");if(!parent)return;
    parent.replaceChildren();const issues=view.input_issues||[];parent.hidden=!issues.length;
    if(!issues.length)return;
    make("h2","需要核对的旅行信息",parent);
    const list=make("ul",undefined,parent);
    for(const issue of issues)make("li",issue.message,list);
    make("p","本次任务已结束。新建任务时，请填写目的城市的具体机场或车站及类型。",parent);
  }
  function status(view){const r=view.run;if(["blocked","incompatible"].includes(r.activity))return activities[r.activity];if(r.cancel_requested&&r.activity!=="terminal")return "正在取消，等待任务安全收束";if(r.activity==="terminal"&&r.delivery_status==="failed")return terminal[r.terminal_code]||"规划失败，未生成完整攻略";return terminal[r.terminal_code]||`${phases[r.phase]||"处理任务"} · ${activities[r.activity]||"处理中"}`;}
  function facts(items,parent){const dl=make("dl",undefined,parent);for(const f of items){make("dt",f.label,dl);make("dd",f.value,dl);}}
  function safeMarkdown(text,parent){
    make("pre",text,parent);
    const urls=[...new Set((text.match(/https?:\/\/[^\s<>"\])]+/g)||[]))];
    for(const value of urls){try{const url=new URL(value);if(!["http:","https:"].includes(url.protocol))continue;const a=make("a","打开来源",parent);a.href=url.href;a.target="_blank";a.rel="noopener noreferrer";make("span"," · ",parent);}catch(e){}}
  }
  async function send(kind,fields,runId){
    if(inflight || pending)return;
    let id;try{id=requestId();}catch(e){message(e.message);return;}
    inflight=true;remember(id,runId);
    const buttons=[...document.querySelectorAll("button")];for(const b of buttons)b.disabled=true;
    try{
      const body={schema_version:"1.0",request_id:id,...fields};
      const receipt=await api(runId?`/api/runs/${runId}/${kind}`:"/api/runs",body);
      if(receipt.status!=="dispatched"){message("接纳仍待核对，请查询原请求。");$("query-request").hidden=false;return;}
      clearPending();message(kind==="cancel"?"正在取消，等待真实终态。":"操作已接纳。");
      if(!runId){location.assign(`/tasks/${receipt.run_id}`);return;}
      await refreshTask();
    }catch(e){
      message(e.message);
      if(e.uncertain){$("query-request").hidden=false;}else{clearPending();}
    }finally{inflight=false;for(const b of buttons)if(b.isConnected)b.disabled=false;}
  }
  async function query(){
    if(!pending)return;
    try{const value=await api(`/api/requests/${pending.request_id}`);
      if(value.status==="dispatched"){clearPending();if(document.body.dataset.page==="home"&&value.run_id)location.assign(`/tasks/${value.run_id}`);else await refreshTask();}
      else if(value.status==="rejected"){clearPending();message("原请求已拒绝，请核对当前状态后重新操作。");}
      else message("原请求仍待核对。可继续查询，请勿重复新建。");
    }catch(e){message(e.message);}
  }
  function clarificationForm(card,form,view){
    const questions=card.questions||[], readers=[], groups=[];
    const fieldId=(field)=>`clarify-${field.replaceAll(".","-")}`;
    const textLimit=(field)=>field==="destination"?64:field.endsWith("hub_name")?128:500;
    const fail=(label)=>{throw new Error(label);};
    const readText=(input,label,limit=500)=>{
      const value=input.value.trim();
      if(!value)fail(`请填写${label}。`);
      if([...value].length>limit)fail(`${label}超过${limit}字，请精简。`);
      return value;
    };
    function group(label,field){
      const set=make("fieldset",undefined,form);set.className="clarification-question";set.dataset.field=field;
      make("legend",label,set);groups.push(set);return set;
    }
    let childInput=null, ageQuestion=null, ageSet=null, ageInputs=[];
    for(const q of questions){
      if(q.kind==="ages"){ageQuestion=q;continue;}
      const set=group(q.label+(q.kind==="multiple"?"（可多选）":""),q.field), id=fieldId(q.field);
      if(["single","multiple"].includes(q.kind)){
        const options=make("div",undefined,set);options.className="choice-grid";
        const inputs=q.options.map(option=>{
          const label=make("label",undefined,options);label.className="choice";
          const input=make("input",undefined,label);input.type=q.kind==="multiple"?"checkbox":"radio";
          input.id=`${id}-${option.value}`;input.name=id;input.value=option.value;
          make("span",option.label,label);return {input,option};
        });
        let custom=null;
        if(q.options.some(option=>option.value==="other")){
          const box=make("div",undefined,set);box.hidden=true;
          const label=make("label","请填写具体说明",box);label.htmlFor=`${id}-other-text`;
          custom=make("input",undefined,box);custom.id=label.htmlFor;custom.type="text";custom.maxLength=1000;custom.disabled=true;
          const update=()=>{const other=inputs.some(item=>item.input.checked&&item.option.value==="other");box.hidden=!other;custom.disabled=!other;custom.required=other;};
          for(const item of inputs)item.input.addEventListener("change",update);
        }
        if(q.kind==="multiple")for(const item of inputs)item.input.addEventListener("change",()=>{
          if(!item.input.checked)return;
          for(const other of inputs)if(other!==item&&(item.option.value==="agent_decides"||other.option.value==="agent_decides"))other.input.checked=false;
        });
        readers.push(()=>{
          const chosen=inputs.filter(item=>item.input.checked);
          if(!chosen.length)fail(`请选择${q.label}。`);
          return `${q.label}：${chosen.map(item=>item.option.value==="other"?readText(custom,q.label):item.option.label).join("、")}`;
        });
      }else{
        const label=make("label",q.label,set);label.htmlFor=id;label.className="visually-hidden";
        const input=make("input",undefined,set);input.id=id;input.required=true;
        input.type=q.kind==="datetime"?"datetime-local":q.kind==="number"?"number":q.kind==="date"?"date":"text";
        if(q.kind==="number"){input.min=String(q.minimum);input.max=String(q.maximum);input.step="1";}
        else if(q.kind==="datetime")input.step="60";
        else if(q.kind==="text"){input.maxLength=textLimit(q.field)*2;input.placeholder=q.field==="destination"?"例如：杭州":q.field.endsWith("hub_name")?"填写具体机场或车站名称":"填写具体内容";}
        if(q.field==="party.children_count")childInput=input;
        readers.push(()=>{
          if(!input.checkValidity())fail(`请核对${q.label}。`);
          const value=readText(input,q.label,q.kind==="text"?textLimit(q.field):500);
          return `${q.label}：${q.kind==="datetime"?value.replace("T"," "):value}`;
        });
      }
    }
    if(childInput||ageQuestion){
      ageSet=group("儿童年龄（每位0–17岁）","party.children_ages");
      const ageGrid=make("div",undefined,ageSet);ageGrid.className="age-grid";
      function renderAges(count){
        if(ageInputs.length===count)return;
        const previous=ageInputs.map(input=>input.value);ageGrid.replaceChildren();ageInputs=[];
        ageSet.hidden=count===0;
        for(let index=0;index<count;index++){
          const label=make("label",`第${index+1}位儿童`,ageGrid);label.htmlFor=`clarify-child-age-${index+1}`;
          const input=make("input",undefined,label);input.id=label.htmlFor;input.type="number";input.min="0";input.max="17";input.step="1";input.required=true;
          input.value=previous[index]||"";ageInputs.push(input);
        }
      }
      ageSet.hidden=true;
      if(childInput)childInput.addEventListener("input",()=>renderAges(childInput.value!==""&&childInput.checkValidity()?Number(childInput.value):0));
      else renderAges(ageQuestion.item_count);
      readers.push(()=>{
        const count=childInput?Number(childInput.value):ageQuestion.item_count;
        if(count===0)return null;
        if(ageInputs.length!==count||ageInputs.some(input=>!input.checkValidity()||input.value===""))fail("请填写每位儿童的年龄（0–17岁）。");
        return `儿童年龄：${ageInputs.map(input=>input.value).join("、")}`;
      });
    }
    let manual=null, notesParent=form;
    if(questions.length){
      notesParent=make("details",undefined,form);notesParent.className="clarification-notes";
      make("summary","其他补充说明（可选）",notesParent);
      const modeLabel=make("label",undefined,notesParent);modeLabel.className="check";
      manual=make("input",undefined,modeLabel);manual.type="checkbox";manual.id="clarify-manual-mode";
      make("span","用文字回答本轮问题",modeLabel);
      make("p","特殊情况可在这里说明；选择文字回答后，请完整回答本轮需要补充的信息。",notesParent).className="muted";
    }
    const label=make("label",questions.length?"补充说明":"回答本轮问题",notesParent);label.htmlFor="clarify-input";
    const notes=make("textarea",undefined,notesParent);notes.id="clarify-input";notes.maxLength=16000;notes.rows=4;notes.required=!questions.length;
    if(manual)manual.addEventListener("change",()=>{for(const set of groups)set.disabled=manual.checked;notes.required=manual.checked;});
    make("button","提交补充",form).type="submit";
    form.addEventListener("submit",e=>{
      e.preventDefault();
      try{
        let value;
        if(!questions.length||manual.checked)value=readText(notes,"本轮回答",8000);
        else{
          const lines=readers.map(read=>read()).filter(Boolean);
          if(notes.value.trim())lines.push(`其他补充说明：${notes.value.trim()}`);
          value=`补充旅行需求：\n${lines.join("\n")}`;
        }
        if([...value].length>8000)fail("补充内容超过8000字，请精简后提交。");
        send("clarify",{pause_generation:view.run.pause_generation,user_message:value},view.run.run_id);
      }catch(error){message(error.message);}
    });
  }
  function interaction(view){
    const key=JSON.stringify({run_id:view.run.run_id,generation:view.run.pause_generation,card:view.card,actions:view.actions});
    if(key===interactionKey)return;
    interactionKey=key;
    const parent=$("interaction");parent.replaceChildren();const c=view.card;
    if(!c)return;
    make("h2",c.title,parent);
    let paragraphs=parent;
    if(c.kind==="clarification"&&(c.questions||[]).length){
      make("p","选择本轮需要补充的信息，填写完成后提交。",parent);
      paragraphs=make("details",undefined,parent);paragraphs.className="clarification-context";
      make("summary","查看本轮问题说明",paragraphs);
    }
    for(const p of c.paragraphs)make("p",p,paragraphs).className="domain-message";facts(c.facts,parent);
    const form=make("form",undefined,parent);
    if(c.kind==="clarification"){
      clarificationForm(c,form,view);
    }else if(c.kind==="confirmation"){
      const label=make("label","希望修改的内容（选择修改时填写）",form);label.htmlFor="revision-input";
      const input=make("textarea",undefined,form);input.id="revision-input";input.maxLength=8000;
      const mapLabel=make("label",undefined,form);const map=make("input",undefined,mapLabel);map.type="checkbox";map.id="map-requested";make("span","希望提供地图链接（信息足够时）",mapLabel);
      const accept=make("button","确认需求",form);accept.type="button";accept.addEventListener("click",()=>send("confirm",{pause_generation:view.run.pause_generation,decision:"accept",revision_text:null,map_requested:map.checked},view.run.run_id));
      const revise=make("button","修改需求",form);revise.type="button";revise.addEventListener("click",()=>{if(!input.value.trim()){message("请填写修改内容。");return;}send("confirm",{pause_generation:view.run.pause_generation,decision:"revise",revision_text:input.value,map_requested:false},view.run.run_id);});
    }else{
      for(const choice of c.choices){const article=make("article",undefined,form);const label=make("label",undefined,article);const radio=make("input",undefined,label);radio.type="radio";radio.name="hotel";radio.value=String(choice.ordinal);make("span",`${choice.ordinal}. ${choice.name}`,label);if(choice.address)make("p",choice.address,article);facts(choice.facts,article);}
      const select=make("button","选择这家酒店",form);select.type="submit";
      form.addEventListener("submit",e=>{e.preventDefault();const chosen=form.querySelector("input[name=hotel]:checked");if(!chosen){message("请先选择一家酒店。");return;}send("hotel",{pause_generation:view.run.pause_generation,decision:"select",ordinal:Number(chosen.value)},view.run.run_id);});
      const decline=make("button","全部放弃",form);decline.type="button";decline.addEventListener("click",()=>send("hotel",{pause_generation:view.run.pause_generation,decision:"decline",ordinal:null},view.run.run_id));
    }
  }
  async function readSummary(){
    if(!current || !$("statistics").open)return;
    try{const data=await api(`/api/runs/${current.run.run_id}/summary`);const names={not_finalized:"尚未产生最终统计",pending_save:"最终统计正在保存",not_saved:"攻略可正常查看，统计未保存",process_history_unavailable:"本进程缺少完整运行历史",saved:"最终统计已保存"};$("summary-status").textContent=names[data.status];const parent=$("summary-content");parent.replaceChildren();
      if(data.summary){const s=data.summary;facts([{label:"总耗时",value:`${s.duration_ms} ms`},{label:"缓存命中",value:String(s.cache_hits)},{label:"观测失败",value:String(s.event_delivery_failures)}],parent);make("h3","安全计数",parent);facts(s.counters.map(c=>({label:c.name,value:String(c.value)})),parent);make("h3","预算快照",parent);make("pre",JSON.stringify(s.budget_snapshots,null,2),parent);make("p","实际费用：未知；这些计数不代表供应商账单。",parent);}
      if(data.status==="pending_save" && !summaryTimer && summaryAttempts<6){summaryAttempts++;summaryTimer=setTimeout(()=>{summaryTimer=null;readSummary();},2000);}
    }catch(e){$("summary-status").textContent=e.message;}
  }
  async function readResult(view){
    if(!view.result_available || markdown!==null)return;
    const data=await api(`/api/runs/${view.run.run_id}/result`);markdown=data.markdown;
    $("result").hidden=false;$("result-markdown").textContent=markdown;
    $("delivery-note").textContent=(data.delivery_status==="degraded"?"部分信息不足，请阅读正文中的降级原因。 ":"")+"费用未核验，请出行前自行确认交通、营业时间和价格。";
    const parent=$("result-days");parent.replaceChildren();for(const d of data.days){const article=make("article",undefined,parent);make("h3",`游览日 ${d.ordinal}`,article);safeMarkdown(d.markdown,article);}
  }
  let refreshing=false;
  async function refreshTask(){
    if(refreshing || document.body.dataset.page!=="task")return;
    refreshing=true;try{const id=location.pathname.split("/").pop();const view=await api(`/api/runs/${id}`);current=view;
      if(view.run.activity==="terminal"){
        const closed=stream;stream=null;if(closed)closed.close();
        if(messageSource==="sse")message("");
      }
      $("task-status").textContent=(view.input_issues||[]).length?"旅行需求未通过校验":status(view);inputIssues(view);interaction(view);const controls=$("controls");controls.replaceChildren();
      for(const kind of ["cancel","recover"]){if(!view.actions.includes(kind))continue;const button=make("button",kind==="cancel"?"取消任务":"明确恢复任务",controls);button.addEventListener("click",()=>send(kind,{},id));}
      await readResult(view);if($("statistics").open)await readSummary();
    }catch(e){message(e.message,"read");}finally{refreshing=false;}
  }
  async function home(){
    try{const view=await api("/api/admission");$("admission").textContent=`剩余额度：全站今日 ${view.global_remaining}，当前浏览器今日 ${view.visitor_remaining}，当前 IP 本小时 ${view.ip_remaining}。`+(view.can_start?"":" 暂不可开始，可查看完整样例。");}catch(e){$("admission").textContent=e.message;}
    $("start-form").addEventListener("submit",e=>{e.preventDefault();send("start",{user_message:$("trip-input").value},null);});
  }
  async function history(cursor=null){
    try{const data=await api("/api/runs"+(cursor?"?cursor="+encodeURIComponent(cursor):""));const parent=$("history-list");parent.replaceChildren();
      if(data.items.length===0)make("p","当前浏览器尚无任务。",parent);
      for(const view of data.items){const article=make("article",undefined,parent);make("h2",status(view),article);const time=(value)=>value?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",dateStyle:"medium",timeStyle:"short"}).format(new Date(value)):"尚未完成";make("p",`创建：${time(view.run.created_at)}；完成：${time(view.run.finalized_at)}`,article);const a=make("a","进入任务",article);a.href=`/tasks/${view.run.run_id}`;}
      $("history-next").hidden=!data.next_cursor;$("history-next").onclick=()=>history(data.next_cursor);
    }catch(e){message(e.message);}
  }
  async function samples(){
    const data=await api("/api/samples");for(const item of data.items){const article=make("article",undefined,$("sample-list"));make("h2",item.title,article);make("p",`生成日期 ${item.generated_date} · ${item.source_kind==="synthetic"?"离线合成模拟样例":"经验证的结果"} · 非实时`,article);safeMarkdown(item.markdown,article);}
  }
  async function init(){
    const page=document.body.dataset.page;
    try{const raw=sessionStorage.getItem("cta_pending");if(raw){const p=JSON.parse(raw);const canonical=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;if(Object.keys(p).every(k=>["request_id","run_id"].includes(k))&&canonical.test(p.request_id)&&(p.run_id===null||canonical.test(p.run_id)))pending=p;}}catch(e){}
    if($("query-request")){$("query-request").onclick=query;if(pending){$("query-request").hidden=false;message("已保留原请求标识，仅查询状态，不自动重发。");}}
    if(page==="samples"){await samples();return;}
    await session();if(pending)await query();
    if(page==="home")await home();else if(page==="history")await history();else{
      await refreshTask();
      $("statistics").addEventListener("toggle",()=>{if($("statistics").open)readSummary();});$("refresh-summary").onclick=()=>{summaryAttempts=0;readSummary();};
      $("copy-result").onclick=async()=>{if(markdown===null)return;try{if(navigator.clipboard){await navigator.clipboard.writeText(markdown);message("已复制完整 Markdown。");return;}}catch(e){}
        const area=make("textarea",undefined,$("result"));area.value=markdown;area.setAttribute("aria-label","完整 Markdown，请手动复制");area.select();let copied=false;try{copied=document.execCommand("copy");}catch(e){}message(copied?"已复制完整 Markdown。":"请从已选中的文本手动复制。");if(copied)area.remove();};
      $("download-result").onclick=()=>{if(markdown===null)return;const url=URL.createObjectURL(new Blob([markdown],{type:"text/markdown;charset=utf-8"}));const a=make("a",undefined,document.body);a.href=url;a.download="旅行攻略.md";a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      if(current&&current.run.activity!=="terminal"){
        const source=new EventSource(`/api/runs/${current.run.run_id}/events`);stream=source;
        source.addEventListener("snapshot",()=>{if(stream===source)refreshTask();});
        source.onerror=()=>{
          if(stream!==source||source.readyState===EventSource.CLOSED||current.run.activity==="terminal"||pending||inflight||messageSource==="read")return;
          message("推送暂时中断，可刷新读取最新状态。","sse");
        };
      }
      setInterval(()=>{if(!inflight&&document.visibilityState==="visible")refreshTask();},10000);
    }
  }
  init().catch(e=>message(e.message));
  addEventListener("pagehide",()=>{const closed=stream;stream=null;if(closed)closed.close();if(summaryTimer)clearTimeout(summaryTimer);});
})();
