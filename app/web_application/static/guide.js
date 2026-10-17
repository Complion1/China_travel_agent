"use strict";
(() => {
  const {make} = window.CTAUI;
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
  function foldGuideSources(parent,root){
    const headings=[...parent.children].filter(node=>node.tagName==="H3"&&node.textContent==="来源清单");
    if(headings.length!==1)return new Set();
    const heading=headings[0],content=[];
    for(let node=heading.nextElementSibling;node&&!/^H[1-3]$/.test(node.tagName);node=node.nextElementSibling)content.push(node);
    if(!content.length||content.some(node=>node.tagName!=="UL"))return new Set();
    const items=content.flatMap(node=>[...node.children]);
    const numbers=items.map(node=>{
      const match=node.tagName==="LI"&&node.textContent.match(/^〔来源 ([1-9]\d{0,3})〕百度地图[｜|]/);
      return match?Number(match[1]):null;
    });
    const ids=new Set(numbers);
    if(!items.length||items.length>1024||numbers.some(value=>value===null||value>1024)||ids.size!==items.length)return new Set();
    const details=make("details");details.className="source-details";
    make("summary","数据来源",details);
    const body=make("div",undefined,details);body.className="source-content";
    parent.insertBefore(details,heading);body.append(heading,...content);
    details.addEventListener("toggle",()=>root.classList.toggle("guide-sources-visible",details.open));
    return ids;
  }
  function compactGuideReferences(parent,sourceIds){
    if(!sourceIds.size)return;
    const walker=document.createTreeWalker(parent,NodeFilter.SHOW_TEXT),nodes=[];
    while(walker.nextNode()){
      const node=walker.currentNode;
      if(!node.parentElement.closest("pre,code,a,.source-details"))nodes.push(node);
    }
    for(const node of nodes){
      const text=node.textContent,fragment=document.createDocumentFragment();
      const references=/地点身份已核验〔来源 ([1-9]\d{0,3})〕；?|〔来源 ([1-9]\d{0,3})〕/g;
      let offset=0;
      for(const match of text.matchAll(references)){
        if(!sourceIds.has(Number(match[1]||match[2])))continue;
        fragment.append(document.createTextNode(text.slice(offset,match.index)));
        const label=make("span",match[0],fragment);label.className="source-reference";
        offset=match.index+match[0].length;
      }
      if(offset){fragment.append(document.createTextNode(text.slice(offset)));node.replaceWith(fragment);}
    }
  }
  function foldTransit(parent){
    const label="乘车顺序（查询时路线说明，班次与实时运营未核验）：";
    for(const paragraph of [...parent.querySelectorAll("p")]){
      const list=paragraph.nextElementSibling;
      if(paragraph.textContent!==label || list?.tagName!=="OL")continue;
      const details=make("details");details.className="transit-details";
      paragraph.before(details);
      make("summary",label,details);details.append(list);paragraph.remove();
    }
  }
  function markdownSections(markdown){
    const buffer=make("div");safeMarkdown(markdown,buffer);
    const nodes=[...buffer.children],headings=nodes.filter(node=>node.tagName==="H3");
    const dayHeading=node=>/^第 ([1-9]\d?) 天｜/.test(node.textContent);
    const days=headings.filter(dayHeading);
    if(!days.length || days.length>60 || days.some((node,index)=>!node.textContent.startsWith(`第 ${index+1} 天｜`)))return null;
    const first=headings.indexOf(days[0]);
    if(days.some((node,index)=>headings[first+index]!==node))return null;
    const end=headings[first+days.length],endIndex=end?nodes.indexOf(end):nodes.length;
    return {
      overview:nodes.slice(0,nodes.indexOf(days[0])),
      days:days.map((node,index)=>({ordinal:index+1,nodes:nodes.slice(nodes.indexOf(node),index+1<days.length?nodes.indexOf(days[index+1]):endIndex)})),
      notes:nodes.slice(endIndex)
    };
  }
  function navigation(nav,articles){
    if(!articles.length){nav.hidden=true;return;}
    nav.hidden=false;
    const links=[...nav.children];
    const activate=index=>{for(const [ordinal,link] of links.entries())if(index===ordinal)link.setAttribute("aria-current","location");else link.removeAttribute("aria-current");};
    links.forEach((link,index)=>link.addEventListener("click",()=>activate(index)));
    activate(0);
    if(typeof IntersectionObserver!=="function")return;
    const observer=new IntersectionObserver(()=>{
      const candidates=articles.map((node,index)=>({index,box:node.getBoundingClientRect()})).filter(item=>item.box.bottom>130);
      if(candidates.length)activate(candidates[0].index);
    },{rootMargin:"-110px 0px -50% 0px"});
    articles.forEach(article=>observer.observe(article));
    addEventListener("pagehide",()=>observer.disconnect(),{once:true});
  }
  function render(markdown,{root,overview,days,notes,nav,prefix,fragments=null}){
    overview.replaceChildren();days.replaceChildren();notes.replaceChildren();nav.replaceChildren();
    overview.classList.add("markdown-body");notes.classList.add("markdown-body");
    overview.hidden=false;notes.hidden=false;
    let sections;
    if(fragments){
      const body=fragments.map(day=>day.markdown).join("\n\n"),boundary=body?markdown.indexOf("\n\n"+body+"\n\n"):-1;
      if(boundary<0){safeMarkdown(markdown,overview);nav.hidden=true;notes.hidden=true;return;}
      safeMarkdown(markdown.slice(0,boundary),overview);
      safeMarkdown(markdown.slice(boundary+body.length+4),notes);
      sections=fragments;
    }else{
      const organized=markdownSections(markdown);
      if(!organized){safeMarkdown(markdown,overview);nav.hidden=true;notes.hidden=true;
        const ids=foldGuideSources(overview,root);compactGuideReferences(overview,ids);foldTransit(overview);return;}
      overview.append(...organized.overview);notes.append(...organized.notes);sections=organized.days;
    }
    const sourceIds=foldGuideSources(notes,root),articles=[];
    for(const section of sections){
      const article=make("article",undefined,days);article.className="day-card markdown-body";article.id=`${prefix}-day-${section.ordinal}`;
      make("p",`第 ${section.ordinal} 天${section.reading?' · '+section.reading.date:''}`,article).className="eyebrow";
      if(section.nodes)article.append(...section.nodes);else safeMarkdown(section.markdown,article);
      if(section.reading){
        const reading=section.reading,heading=article.querySelector("h3");
        if(heading?.textContent===`第 ${section.ordinal} 天｜${reading.date}｜${reading.theme}`)heading.textContent=reading.theme;
        const meta=make("p",`${reading.visit_count} 项活动 · ${reading.route_count} 段交通 · 日程预留交通 ${reading.transport_minutes} 分钟`);
        meta.className="day-reading";article.insertBefore(meta,article.querySelector(".eyebrow").nextSibling);
      }
      const link=make("a",`第 ${section.ordinal} 天${section.reading?' · '+section.reading.date.slice(5):''}`,nav);link.href=`#${article.id}`;
      articles.push(article);
    }
    for(const part of [overview,days,notes]){compactGuideReferences(part,sourceIds);foldTransit(part);}
    navigation(nav,articles);
  }
  function sample(markdown,parent,prefix){
    const overview=make("section",undefined,parent);overview.className="result-overview";
    const nav=make("nav",undefined,parent);nav.className="day-navigation";nav.setAttribute("aria-label","每日行程导航");
    const days=make("div",undefined,parent);
    const notes=make("section",undefined,parent);notes.className="result-notes";
    render(markdown,{root:parent,overview,days,notes,nav,prefix});
  }
  window.CTAGuide = Object.freeze({render,sample});
})();
