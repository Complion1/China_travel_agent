"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const make = (tag, text, parent) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(parent)parent.append(e); return e; };
  let csrf=null, inflight=false, pending=null, current=null, markdown=null, stream=null, summaryTimer=null, summaryAttempts=0, interactionKey=null;
  let messageSource=null;
  let lastObservedPhase=null;
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
  const activities={idle:"准备中",executing:"正在执行",awaiting_user:"等待你的选择",recovery_required:"需要明确恢复",cancelling:"正在取消",blocked:"当前任务无法安全继续",incompatible:"任务版本不兼容",terminal:"已结束"};
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
  const flowSteps=["requirements","hotel","research","planning","formatting","delivered"];
  const phaseSteps={requirements:0,hotel_selection:1,candidate_research:2,itinerary_planning:3,validation:3,budget_evaluation:3,output_formatting:4};
  const phaseDescriptions={requirements:"正在理解你的旅行条件，整理需要确认的信息。",hotel_selection:"正在查询酒店候选，找到后会请你选择。",candidate_research:"正在研究景点、餐饮与附近备选，为每日行程准备候选。",itinerary_planning:"正在安排每天的游览顺序、活动时间与交通路线。",validation:"正在核验行程与已有事实证据。",budget_evaluation:"正在检查行程的执行预算。",output_formatting:"正在整理已核验的行程，生成完整攻略。"};
  function flowConnections(){
    const canvas=$("workflow-canvas"),svg=$("workflow-edges");if(!canvas||canvas.getBoundingClientRect().width===0)return;
    const root=canvas.getBoundingClientRect(),nodes=flowSteps.map(step=>canvas.querySelector(`[data-step="${step}"]`));
    svg.setAttribute("viewBox",`0 0 ${root.width} ${root.height}`);const paths=$("workflow-paths");paths.replaceChildren();
    const box=node=>{const r=node.getBoundingClientRect();return {left:r.left-root.left,right:r.right-root.left,top:r.top-root.top,bottom:r.bottom-root.top,x:r.left-root.left+r.width/2,y:r.top-root.top+r.height/2};};
    function edge(d,state,loop=false){const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",d);path.setAttribute("class",loop?"flow-edge flow-loop":"flow-edge");path.dataset.state=state;path.setAttribute("marker-end","url(#flow-arrow)");paths.append(path);}
    for(let index=0;index<nodes.length-1;index++){
      const a=box(nodes[index]),b=box(nodes[index+1]);
      const d=Math.abs(a.x-b.x)<20?`M${a.x} ${a.y<b.y?a.bottom:a.top}V${a.y<b.y?b.top-8:b.bottom+8}`:`M${a.right} ${a.y}H${b.left-8}`;
      const next=nodes[index+1].dataset.state,state=nodes[index].dataset.state==="complete"?(next==="running"?"running":"complete"):"pending";edge(d,state);
    }
    const planning=box(nodes[3]),research=box(nodes[2]),formatting=box(nodes[4]);
    if(formatting.left>planning.right){const x=planning.right+(formatting.left-planning.right)/2;edge(`M${planning.right} ${planning.y+18}H${x-12}Q${x} ${planning.y+18} ${x} ${planning.y+30}V${research.y-12}Q${x} ${research.y} ${x-12} ${research.y}H${research.right+8}`,"pending",true);const label=document.createElementNS("http://www.w3.org/2000/svg","text");label.setAttribute("x",x);label.setAttribute("y",(planning.y+research.y)/2);label.setAttribute("class","flow-loop-label");label.textContent="补充研究";paths.append(label);}
  }
  function workflow(view){
    const panel=$("workflow");if(!panel)return;panel.hidden=false;panel.dataset.connection="available";
    const r=view.run,phase=r.phase||(r.activity==="executing"&&!r.cancel_requested?view.progress_phase:null),ready=view.result_available&&r.activity==="terminal"&&["demo_ready","degraded"].includes(r.delivery_status),observedIndex=ready?5:phaseSteps[phase],delivered=ready&&markdown!==null;
    if(lastObservedPhase?.run_id!==r.run_id)lastObservedPhase=null;
    if(observedIndex!==undefined)lastObservedPhase={run_id:r.run_id,index:observedIndex};
    const unknown=observedIndex===undefined,index=unknown?lastObservedPhase?.index:observedIndex;
    const cancelling=r.activity!=="terminal"&&(r.cancel_requested||r.activity==="cancelling"),stopped=["blocked","incompatible","recovery_required"].includes(r.activity)||cancelling||r.activity==="terminal";
    const state=unknown?"unknown":delivered?"complete":ready||cancelling?"waiting":r.activity==="executing"?"running":r.activity==="awaiting_user"?"waiting":stopped?"stopped":"pending";
    const labels={complete:"已完成",observed:"最近确认的阶段",running:"进行中",waiting:r.cancel_requested?"正在取消":"等待你的操作",stopped:r.activity==="recovery_required"?"等待明确恢复":r.activity==="terminal"?"已停止":"无法安全继续",pending:"待开始"};
    for(let ordinal=0;ordinal<flowSteps.length;ordinal++){
      const node=panel.querySelector(`[data-step="${flowSteps[ordinal]}"]`);
      const nodeState=delivered?"complete":ordinal<index?"complete":ordinal===index?(unknown?"observed":state):"pending";
      node.dataset.state=nodeState;node.querySelector(".flow-node-status").textContent=labels[nodeState];
      if(!delivered&&!unknown&&ordinal===index)node.setAttribute("aria-current","step");else node.removeAttribute("aria-current");
    }
    if(ready&&!delivered){const node=panel.querySelector('[data-step="delivered"]');node.querySelector(".flow-node-status").textContent="正在读取完整攻略";}
    const active=index===undefined?"处理旅行任务":panel.querySelector(`[data-step="${flowSteps[index]}"] h3`).textContent;
    $("phase-title").textContent=unknown?"当前阶段暂不可读":delivered?"完整攻略已交付":ready?"正在读取完整攻略":active;
    $("phase-badge").textContent=delivered?(r.delivery_status==="degraded"?"已完成 · 部分信息不足":"已完成"):ready?"读取攻略":cancelling?"正在取消":activities[r.activity]||"正在读取";
    $("task-title").textContent=delivered?"旅行攻略已完成":ready?"正在读取完整攻略":cancelling?"正在取消旅行规划":r.activity==="executing"&&!unknown?"正在"+active:r.activity==="executing"?"正在处理旅行规划":r.activity==="awaiting_user"?"等待你的选择与确认":r.activity==="recovery_required"?"任务等待明确恢复":["blocked","incompatible"].includes(r.activity)?"任务无法安全继续":r.activity==="terminal"?(r.delivery_status==="cancelled"?"旅行规划已取消":"本次规划未完成"):"旅行规划任务";
    panel.dataset.state=state;
    let description=unknown?"当前具体阶段暂不可读。流程图仅保留最近一次确认的阶段，不表示任务仍在该阶段。":phaseDescriptions[phase];
    if(delivered)description="旅行总览、每日行程与返程提醒已整理在下方。复制或下载可以保留完整攻略。";
    else if(cancelling&&r.activity!=="terminal")description="已收到取消请求，正在等待任务安全收束；确认终态后才会显示已取消。";
    else if(r.activity==="awaiting_user"){
      const hints={clarification:["clarify","请在下方补充本轮旅行信息，填写完成后继续。"],confirmation:["confirm","请在下方确认旅行需求，或填写希望修改的内容。"],hotel:["hotel","请在下方选择酒店，随后继续研究游览地点与规划行程。"]},hint=hints[view.card?.kind];
      description=hint&&view.actions.includes(hint[0])?hint[1]:"任务正在等待操作，暂时没有可用的选项。请稍后刷新读取最新状态。";
    }
    else if(r.activity==="recovery_required")description="任务等待明确恢复。请核对下方操作，不会因刷新页面自动重试。";
    else if(["blocked","incompatible"].includes(r.activity))description="当前任务无法安全继续，请查看任务状态与下方可用操作。";
    else if(r.activity==="terminal")description=view.result_available?"正在读取完整攻略，只有读取成功后才标记交付完成。":status(view)+"。当前没有可交付的完整攻略。";
    $("phase-description").textContent=description;
    $("phase-next").textContent=delivered?"规划结束后，仍请在出行前复核营业、预约与价格。":!stopped&&r.activity!=="awaiting_user"&&!unknown?"页面会随真实任务状态更新。模型与地图查询可能需要一些时间。":"刷新或关闭页面不会取消任务。";
    flowConnections();
  }
  function workflowUnavailable(){
    if(!$("workflow"))return;if(!current){$("workflow").hidden=true;return;}
    $("workflow").dataset.connection="unavailable";$("phase-badge").textContent="状态读取中断";
    $("phase-description").textContent="暂时无法读取最新状态。流程图保留最近一次确认的阶段，恢复读取后更新。";
  }
  function decodeEntities(text){
    const names={lt:"<",gt:">",amp:"&",quot:'"',apos:"'"};
    return text.replace(/&(lt|gt|amp|quot|apos|#\d{1,7}|#x[\da-f]{1,6});/gi,(raw,name)=>{
      if(name[0]!=="#")return names[name.toLowerCase()];const point=name[1].toLowerCase()==="x"?parseInt(name.slice(2),16):Number(name.slice(1));
      return point>0&&point<=0x10ffff&&!(point>=0xd800&&point<=0xdfff)?String.fromCodePoint(point):raw;
    });
  }
  function inlineMarkdown(text,parent){
    // Native DOM only: never interpret HTML or enable images and script URLs.
    const tokens=/\\([\\`*_{}\[\]()#+\-.!|:\/>])|`([^`\n]{1,8192})`|\*\*([^*\n]{1,8192})\*\*|__([^_\n]{1,8192})__|\[([^\]\n]{1,512})\]\(([^)\s]{1,2048})\)|(https?:\/\/[^\s<>"\]\)\\]{1,2048})/g;
    let offset=0,match;const literal=value=>parent.append(document.createTextNode(decodeEntities(value)));
    while((match=tokens.exec(text))!==null){
      literal(text.slice(offset,match.index));offset=tokens.lastIndex;
      if(match[1])literal(match[1]);else if(match[2])make("code",match[2],parent);else if(match[3]||match[4])make("strong",decodeEntities(match[3]||match[4]),parent);
      else{
        const label=match[5]||match[7],value=decodeEntities(match[6]||match[7]);let url=null;
        try{const candidate=new URL(value);if(["http:","https:"].includes(candidate.protocol))url=candidate;}catch(e){}
        if(url){const link=make("a",decodeEntities(label),parent);link.href=url.href;link.target="_blank";link.rel="noopener noreferrer";}
        else literal(match[0]);
      }
    }
    literal(text.slice(offset));
  }
  function safeMarkdown(text,parent){
    parent.classList.add("markdown-body");const lines=text.replace(/\r\n?/g,"\n").split("\n");
    const item=/^(\s*)([-+*]|\d+[.)])\s+(.+)$/;
    const separator=/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
    const cells=line=>line.trim().replace(/^\||\|$/g,"").split(/(?<!\\)\|/).map(value=>value.trim());
    const block=line=>!line.trim()||/^(#{1,6}\s|\s*```|\s*>)/.test(line)||item.test(line);
    let index=0,timeline=false;
    function list(){
      const stack=[];const base=lines[index].match(item)[1].length;
      while(index<lines.length){
        const line=lines[index],found=line.match(item);
        if(!line.trim()&&index+1<lines.length&&(item.test(lines[index+1])||/^\s+\S/.test(lines[index+1]))){index++;continue;}
        if(!found){if(stack.length&&line.trim()&&line.match(/^\s*/)[0].length>base){inlineMarkdown(line.trim(),make("p",undefined,stack[stack.length-1].last));index++;continue;}break;}
        const indent=found[1].length,tag=/\d/.test(found[2])?"ol":"ul";
        while(stack.length&&indent<stack[stack.length-1].indent)stack.pop();
        if(stack.length&&indent===stack[stack.length-1].indent&&tag!==stack[stack.length-1].tag)stack.pop();
        if(!stack.length||indent>stack[stack.length-1].indent){const owner=stack.length?stack[stack.length-1].last:parent,node=make(tag,undefined,owner);if(tag==="ol")node.start=parseInt(found[2],10);if(timeline&&!stack.length)node.className="itinerary-timeline";stack.push({indent,tag,node,last:null});}
        const level=stack[stack.length-1],li=make("li",undefined,level.node);level.last=li;
        const clock=timeline&&stack.length===1?found[3].match(/^(.{1,100}（北京时间）)｜(.*)$/):null;
        if(clock){make("span",clock[1],li).className="timeline-time";const content=make("div",undefined,li);inlineMarkdown(clock[2],content);}else inlineMarkdown(found[3],li);
        index++;
      }
    }
    while(index<lines.length){
      const line=lines[index];if(!line.trim()){index++;continue;}
      const heading=line.match(/^(#{1,6})\s+(.+)$/);
      if(heading){inlineMarkdown(heading[2],make(`h${Math.min(6,heading[1].length+1)}`,undefined,parent));timeline=heading[2]==="主行程";index++;continue;}
      const fence=line.match(/^\s*(`{3,})/);
      if(fence){const content=[];index++;while(index<lines.length&&!lines[index].trim().startsWith(fence[1]))content.push(lines[index++]);make("code",content.join("\n"),make("pre",undefined,parent));if(index<lines.length)index++;continue;}
      if(index+1<lines.length&&line.includes("|")&&separator.test(lines[index+1])){
        const wrap=make("div",undefined,parent);wrap.className="markdown-table";wrap.tabIndex=0;wrap.setAttribute("aria-label","横向滚动查看完整表格");const table=make("table",undefined,wrap),row=make("tr",undefined,make("thead",undefined,table));
        for(const value of cells(line)){const th=make("th",undefined,row);th.scope="col";inlineMarkdown(value,th);}const body=make("tbody",undefined,table);index+=2;
        while(index<lines.length&&lines[index].trim()&&lines[index].includes("|")){const tr=make("tr",undefined,body);for(const value of cells(lines[index++]))inlineMarkdown(value,make("td",undefined,tr));}continue;
      }
      if(/^\s*>/.test(line)){const quote=make("blockquote",undefined,parent);while(index<lines.length&&/^\s*>/.test(lines[index]))inlineMarkdown(lines[index++].replace(/^\s*>\s?/,""),make("p",undefined,quote));continue;}
      if(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)){make("hr",undefined,parent);index++;continue;}
      if(item.test(line)){list();continue;}
      const paragraph=[line];index++;while(index<lines.length&&!block(lines[index])&&!(index+1<lines.length&&lines[index].includes("|")&&separator.test(lines[index+1])))paragraph.push(lines[index++]);inlineMarkdown(paragraph.join("\n"),make("p",undefined,parent));
    }
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
    const parent=$("result-days"),overview=$("result-overview"),notes=$("result-notes"),nav=$("result-day-nav");
    parent.replaceChildren();overview.replaceChildren();notes.replaceChildren();nav.replaceChildren();
    const body=data.days.map(day=>day.markdown).join("\n\n"),boundary=body?markdown.indexOf("\n\n"+body+"\n\n"):-1;
    if(boundary<0){safeMarkdown(markdown,overview);nav.hidden=true;notes.hidden=true;}
    else{
      safeMarkdown(markdown.slice(0,boundary),overview);safeMarkdown(markdown.slice(boundary+body.length+4),notes);
      for(const d of data.days){const article=make("article",undefined,parent);article.className="day-card";article.id=`itinerary-day-${d.ordinal}`;make("p",`第 ${d.ordinal} 天`,article).className="eyebrow";safeMarkdown(d.markdown,article);const link=make("a",`第 ${d.ordinal} 天`,nav);link.href=`#${article.id}`;}
    }
    workflow(view);
  }
  let refreshing=false;
  async function refreshTask(){
    if(refreshing || document.body.dataset.page!=="task")return;
    refreshing=true;try{const id=location.pathname.split("/").pop();const view=await api(`/api/runs/${id}`);current=view;
      if(view.run.activity==="terminal"){
        const closed=stream;stream=null;if(closed)closed.close();
        if(messageSource==="sse")message("");
      }
      $("task-status").textContent=(view.input_issues||[]).length?"旅行需求未通过校验":status(view);workflow(view);inputIssues(view);interaction(view);const controls=$("controls");controls.replaceChildren();
      for(const kind of ["cancel","recover"]){if(!view.actions.includes(kind))continue;const button=make("button",kind==="cancel"?"取消任务":"明确恢复任务",controls);button.addEventListener("click",()=>send(kind,{},id));}
      await readResult(view);if($("statistics").open)await readSummary();
    }catch(e){workflowUnavailable();message(e.message,"read");}finally{refreshing=false;}
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
      if($("workflow-canvas")&&typeof ResizeObserver==="function")new ResizeObserver(flowConnections).observe($("workflow-canvas"));
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
