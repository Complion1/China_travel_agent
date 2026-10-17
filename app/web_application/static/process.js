/*
 * Timer and visibility handling adapted from Waku main.js (startTimers/stopTimers).
 * Source: ShenSeanChen/waku-agent, 4b69b321324750f231fdfd72a83b38ad7301dcc3.
 * MIT License
 * Copyright (c) 2026 Sean Chen (ShenSeanChen)
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
"use strict";
(() => {
  const {$,make,moveFocus,time} = window.CTAUI;
  function create({request,status,activities}) {
    let current=null,markdown=null,lastObservedPhase=null,selected=null,after=0,epoch=0,loading=false;
    let refreshTimer=null,terminalReads=0,settled=false,lastRead=null,collapsed=false,lastSnapshot=null;
    const records=new Map(),panes=new Map();
    const labels={proposed:"拟执行 / 待确认",observed:"已保存观察",accepted:"已接受",rejected:"未通过 / 待修正",failed:"失败记录"};
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
    panel.dataset.state=state;
    let description=unknown?"当前具体阶段暂不可读。流程图仅保留最近一次确认的阶段，不表示任务仍在该阶段。":phaseDescriptions[phase];
    if(delivered)description="旅行总览、每日行程与返程提醒已整理完成。复制或下载可以保留完整攻略。";
    else if(cancelling&&r.activity!=="terminal")description="已收到取消请求，正在等待任务安全收束；确认终态后才会显示已取消。";
    else if(r.activity==="awaiting_user"){
      const hints={clarification:["clarify","请补充本轮旅行信息，填写完成后继续。"],confirmation:["confirm","请确认旅行需求，或填写希望修改的内容。"],hotel:["hotel","请选择酒店，随后继续研究游览地点与规划行程。"]},hint=hints[view.card?.kind];
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


    const visible=()=>Boolean(selected && current && !$('process-details').hidden && $('process-overview').open && !document.hidden);
    function stopTimers() {
      clearInterval(refreshTimer);
      refreshTimer=null;
    }
    function startTimers() {
      if(refreshTimer || !visible() || settled || terminalReads>=12)return;
      refreshTimer=setInterval(load,5000);
    }
    function step(record) {return record.body.kind==='result_index'?'delivered':flowSteps[phaseSteps[record.body.phase]];}
    function showState(text) {if($('progress-state'))$('progress-state').textContent=text;}
    function render() {
      if(!selected)return;
      let pane=panes.get(selected);
      if(!pane){pane=make('div',undefined,$('public-record-list'));pane.dataset.recordPhase=selected;panes.set(selected,pane);}
      for(const [key,value] of panes)value.hidden=key!==selected;
      const matching=[...records.values()].filter(item=>step(item)===selected);
      for(const item of matching){
        if(pane.querySelector(`[data-sequence="${item.sequence}"]`))continue;
        const row=make('details',undefined,pane);row.className='fact-record';row.dataset.sequence=String(item.sequence);row.dataset.status=item.body.status;
        const summary=make('summary',undefined,row);
        make('span',item.body.title,summary).className='fact-title';
        make('span',labels[item.body.status],summary).className='fact-status';
        make('p',time(item.occurred_at),row).className='record-time';
        const list=make('dl',undefined,row);
        for(const fact of item.body.facts){make('dt',fact.label,list);make('dd',fact.value,list);}
      }
      $('progress-empty').hidden=matching.length>0;
      $('progress-empty').textContent=records.size?'这个阶段尚无已保存的处理记录。':'尚无已保存的处理记录。';
      for(const node of document.querySelectorAll('.flow-node')){
        const count=[...records.values()].filter(item=>step(item)===node.dataset.step).length;
        let badge=node.querySelector('.record-count');
        if(!badge){badge=make('span',undefined,node);badge.className='record-count';}
        badge.textContent=count?`${count} 条`:'';
      }
    }
    async function load() {
      if(!visible() || loading || settled || terminalReads>=12)return;
      const generation=epoch,runId=current.run.run_id;
      loading=true;
      try{
        let page;
        do{
          page=await request(`/api/runs/${runId}/progress?after=${after}`);
          if(generation!==epoch)return;
          if(page.run_id!==runId || !Array.isArray(page.items) || !Number.isSafeInteger(page.latest_sequence))throw new Error('过程资料格式不可读。');
          for(const item of page.items){
            if(!Number.isSafeInteger(item.sequence) || item.sequence<1 || item.sequence>512)throw new Error('过程记录顺序不可读。');
            if(!records.has(item.sequence))records.set(item.sequence,item);
          }
          const previous=after;
          if(page.items.length)after=Math.max(after,...page.items.map(item=>item.sequence));
          render();
          if(page.next_after!==null && (page.next_after!==after || after<=previous))throw new Error('过程分页暂不可读。');
        }while(page.next_after!==null && visible());
        lastRead=new Date();
        settled=Boolean(page.settled && page.next_after===null && after>=page.latest_sequence);
        const coverage=page.coverage==='limited'?'部分记录未保存。':page.coverage==='unavailable'?'这个任务没有可读取的过程资料。':'仅展示已保存的业务处理记录。';
        showState(`${coverage} ${settled?'终态记录已收束。':terminalReads>=12?'过程记录尚未收束，可重新读取。':''} 最近读取 ${time(lastRead)}。`);
        if(settled || terminalReads>=12)stopTimers();
      }catch(error){if(generation===epoch)showState(`过程详情读取失败。${lastRead?'保留上次读取的记录。':''}可点击重新读取。`);}
      finally{
        loading=false;
        if(generation===epoch && current.run.activity==='terminal' && !settled){
          terminalReads++;
          if(terminalReads>=12){stopTimers();showState('过程记录尚未收束，已暂停自动读取。可点击重新读取。');}
        }
      }
    }
    function select(node) {
      selected=node.dataset.step;
      for(const item of document.querySelectorAll('.flow-node'))item.setAttribute('aria-expanded',String(item===node));
      $('process-details').hidden=false;
      $('detail-title').textContent=node.querySelector('h3').textContent;
      render();load();startTimers();
      if(matchMedia('(max-width:850px)').matches)$('process-details').scrollIntoView({block:'nearest'});
    }
    function close() {
      const node=document.querySelector(`.flow-node[data-step="${selected}"]`);
      $('process-details').hidden=true;
      if(node){node.setAttribute('aria-expanded','false');node.focus();}
      stopTimers();
    }
    for(const node of document.querySelectorAll('.flow-node')){
      node.setAttribute('role','button');node.tabIndex=0;node.setAttribute('aria-controls','process-details');node.setAttribute('aria-expanded','false');
      node.addEventListener('click',()=>select(node));
      node.addEventListener('keydown',event=>{
        if(event.key==='Enter'||event.key===' '){event.preventDefault();select(node);}
        else moveFocus(event,[...document.querySelectorAll('.flow-node')]);
      });
    }
    if($('close-details')){
      $('close-details').addEventListener('click',close);
      $('reload-progress').addEventListener('click',()=>{terminalReads=0;settled=false;load();startTimers();});
      $('process-details').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
      $('process-overview').addEventListener('toggle',()=>{
        if($('process-overview').open){flowConnections();load();startTimers();}else stopTimers();
      });
    }
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden)stopTimers();else{load();startTimers();}
    });
    addEventListener('pagehide',stopTimers);
    return {
      update(view,loaded){
        if(!current || current.run.run_id!==view.run.run_id){epoch++;records.clear();panes.clear();after=0;settled=false;terminalReads=0;collapsed=false;if($('public-record-list'))$('public-record-list').replaceChildren();}
        current=view;markdown=loaded?'loaded':null;workflow(view);
        lastSnapshot=new Date();
        if($('task-title'))$('task-title').textContent=view.trip?`${view.trip.city} · ${view.trip.days}日旅行`:'旅行规划任务';
        if($('task-dates')){
          $('task-dates').hidden=!view.trip;
          $('task-dates').textContent=view.trip?`${view.trip.start_date} — ${view.trip.end_date}`:'';
        }
        if($('process-overview')){
          document.body.dataset.taskState=loaded?'delivered':view.run.activity;
          if(loaded&&!collapsed){collapsed=true;$('process-overview').open=false;stopTimers();}
          $('process-summary-status').textContent=loaded?'已交付，可回看处理记录':activities[view.run.activity]||'读取状态中';
        }
        render();startTimers();
      },
      unavailable(){
        workflowUnavailable();
        if(lastSnapshot && $('phase-next'))$('phase-next').textContent=`最近成功读取 ${time(lastSnapshot)}。可使用重新读取按钮检查连接。`;
      },
      layout:flowConnections
    };
  }
  window.CTAProcess=Object.freeze({create});
})();
