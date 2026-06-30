"""Knowledge Graph v2.0 — 自包含交互 HTML renderer：**只消费 graph-data**（spec §2）。

力导向布局（vanilla JS、零依赖、无构建链、无 CDN、不 fetch、不读 Markdown）：节点力学排布、可拖拽/
滚轮缩放/拖动画布、悬停高亮邻居、点击节点出详情并用 `obsidian://open?path=` 直接跳到对应 Obsidian
笔记（双击亦跳转）。社区配色 + 图例 + 搜索 + 社区过滤 + 学习路径高亮。>500 节点降级（只渲染社区代表
节点 + 学习路径）。内嵌 JSON 对 `</` 安全转义，避免提前闭合 `</script>`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HTML_FILE = "knowledge-graph.generated.html"
MAX_HTML_NODES = 500
MAX_HTML_EDGES = 1200

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>知识图谱（离线交互）</title>
<style>
 *{box-sizing:border-box}
 html,body{margin:0;height:100%;font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;color:#1d2430}
 #app{display:flex;height:100vh}
 #side{width:312px;flex:none;border-right:1px solid #e6e8eb;padding:14px;overflow:auto;background:#fbfcfd}
 #stage{flex:1;position:relative;overflow:hidden;background:radial-gradient(circle at 50% 40%,#fff,#f3f5f7)}
 svg{width:100%;height:100%;display:block;cursor:grab}
 svg.panning{cursor:grabbing}
 h1{font-size:15px;margin:0 0 10px}
 .muted{color:#7a8694;font-size:12px}
 input,select,button{width:100%;margin:5px 0;padding:7px 8px;border:1px solid #d6dadf;border-radius:6px;font-size:13px;background:#fff}
 button{cursor:pointer}
 button:hover{background:#f0f3f6}
 .banner{background:#fff3cd;border:1px solid #ffe69c;padding:8px;margin-bottom:10px;font-size:12px;border-radius:6px}
 .edge{stroke:#c4ccd4;fill:none}
 .edge.depends_on{stroke:#e07b39}
 .edge.contrasts{stroke:#6a8caf;stroke-dasharray:5 4}
 .edge.faded{opacity:.06}
 .edge.lit{stroke:#d83b3b;stroke-width:2.2}
 .node circle{stroke:#fff;stroke-width:1.5;cursor:pointer}
 .node text{font-size:11px;fill:#2b3440;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round;pointer-events:none}
 .node.faded{opacity:.12}
 .node.lit circle{stroke:#d83b3b;stroke-width:2.5}
 #legend{margin-top:12px;font-size:11px;color:#55606c;line-height:1.9}
 .sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle}
 #detail{margin-top:12px;padding-top:10px;border-top:1px solid #e6e8eb;font-size:12.5px;line-height:1.6}
 #detail .ttl{font-size:14px;font-weight:600}
 #detail .pathline{color:#7a8694;font-size:11.5px;word-break:break-all;margin:4px 0}
 a.obs{display:inline-block;margin:6px 0;padding:6px 9px;background:#6c63ff;color:#fff;border-radius:6px;text-decoration:none;font-size:12.5px}
 a.obs:hover{background:#574fe0}
 #hint{position:absolute;left:12px;bottom:10px;font-size:11px;color:#9aa4af;pointer-events:none}
</style>
</head>
<body>
<div id="app">
 <div id="side">
  <h1>知识图谱</h1>
  __DEGRADED_BANNER__
  <input id="search" type="search" placeholder="搜索标签 / 别名 / 路径">
  <select id="community-filter"><option value="">全部社区</option></select>
  <button id="learning-path">高亮学习路径</button>
  <button id="reset">重置视图</button>
  <div id="legend">
   <div><span class="sw" style="background:#e07b39"></span>depends_on（前置）
        <span class="sw" style="background:#6a8caf"></span>contrasts（对比）</div>
   <div id="comm-legend"></div>
  </div>
  <div id="detail" class="muted">点击节点查看详情；点击「在 Obsidian 中打开」跳到该笔记。</div>
 </div>
 <div id="stage">
  <svg id="graph"><g id="view"><g id="edges"></g><g id="nodes"></g></g></svg>
  <div id="hint">拖动节点 · 滚轮缩放 · 拖空白处平移 · 双击节点跳 Obsidian</div>
 </div>
</div>
<script id="graph-data" type="application/json">__PAYLOAD__</script>
<script>
"use strict";
window.__GRAPH_DEGRADED__ = __DEGRADED_FLAG__;
window.__VAULT_ROOT__ = __VAULT_ROOT_JSON__;
(function(){
 var SVGNS="http://www.w3.org/2000/svg";
 var raw = JSON.parse(document.getElementById("graph-data").textContent);
 var VAULT = window.__VAULT_ROOT__ || "";
 var PALETTE=["#4e79a7","#59a14f","#e15759","#f28e2b","#b07aa1","#76b7b2","#edc948","#ff9da7","#9c755f","#7b6cf6","#2aa39a","#d4708a"];
 var commIdx={}; (raw.communities||[]).forEach(function(c,i){ commIdx[c.id]=i; });
 var commLabel={}; (raw.communities||[]).forEach(function(c){ commLabel[c.id]=c.label||c.id; });
 function color(d){ var i=commIdx[d.community_id]; return PALETTE[((i==null?0:i)%PALETTE.length)]; }

 // 降级：只渲染社区代表节点 + 学习路径节点
 var keep=null;
 if(window.__GRAPH_DEGRADED__){ keep={}; (raw.communities||[]).forEach(function(c){ (c.representative_node_ids||[]).forEach(function(i){keep[i]=1;}); });
   (raw.learning_paths||[]).forEach(function(p){ (p.node_ids||[]).forEach(function(i){keep[i]=1;}); }); }
 var nodeList=(raw.nodes||[]).filter(function(n){ return !keep||keep[n.id]; });
 var N={}; var nodes=nodeList.map(function(n){ var o={d:n,x:0,y:0,vx:0,vy:0,fixed:false}; N[n.id]=o; return o; });
 var links=(raw.edges||[]).filter(function(e){ return N[e.source]&&N[e.target]; })
            .map(function(e){ return {s:N[e.source],t:N[e.target],e:e}; });
 var deg={}; links.forEach(function(l){ deg[l.s.d.id]=(deg[l.s.d.id]||0)+1; deg[l.t.d.id]=(deg[l.t.d.id]||0)+1; });
 var nb={}; nodes.forEach(function(o){ nb[o.d.id]={}; }); links.forEach(function(l){ nb[l.s.d.id][l.t.d.id]=1; nb[l.t.d.id][l.s.d.id]=1; });

 var W=window.innerWidth-312, H=window.innerHeight; if(W<300)W=300;
 // 初始布局：按社区分簇成环，避免初始全重叠
 var cN=Math.max(1,(raw.communities||[]).length);
 nodes.forEach(function(o,i){ var ci=commIdx[o.d.community_id]||0; var ang=ci/cN*Math.PI*2;
   var cx=W/2+Math.cos(ang)*Math.min(W,H)*0.28, cy=H/2+Math.sin(ang)*Math.min(W,H)*0.28;
   var a2=i*2.399963, rr=10+(i%23)*2.2; o.x=cx+Math.cos(a2)*rr; o.y=cy+Math.sin(a2)*rr; });

 function radius(o){ return 7+Math.min(10,(deg[o.d.id]||0))*1.1+(o.d.type==="topic"?3:0)+(o.d.type==="overview"?4:0); }

 function tick(a){
  for(var i=0;i<nodes.length;i++){ for(var j=i+1;j<nodes.length;j++){ var p=nodes[i],q=nodes[j];
    var dx=p.x-q.x,dy=p.y-q.y,d2=dx*dx+dy*dy+0.01,d=Math.sqrt(d2); var f=2600/d2; var fx=dx/d*f,fy=dy/d*f;
    p.vx+=fx;p.vy+=fy;q.vx-=fx;q.vy-=fy; } }
  links.forEach(function(l){ var dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)+0.01;
    var rest=130-40*(l.e.weight||0.4); var f=(d-rest)*0.03; var fx=dx/d*f,fy=dy/d*f;
    l.s.vx+=fx;l.s.vy+=fy;l.t.vx-=fx;l.t.vy-=fy; });
  nodes.forEach(function(o){ o.vx+=(W/2-o.x)*0.0016; o.vy+=(H/2-o.y)*0.0016;
    if(o.fixed){o.vx=0;o.vy=0;return;} o.vx*=0.86;o.vy*=0.86;
    var sp=Math.sqrt(o.vx*o.vx+o.vy*o.vy); if(sp>30){o.vx=o.vx/sp*30;o.vy=o.vy/sp*30;}
    o.x+=o.vx*a; o.y+=o.vy*a; });
 }
 var a0=0.35; for(var s=0;s<600;s++){ tick(a0); a0*=0.992; }   // 冷却收敛（确定性、不发散）

 var svg=document.getElementById("graph"), view=document.getElementById("view");
 var gE=document.getElementById("edges"), gN=document.getElementById("nodes");
 function el(t,a){ var e=document.createElementNS(SVGNS,t); for(var k in a){ e.setAttribute(k,a[k]); } return e; }
 var lineEls=links.map(function(l){ var ln=el("line",{}); ln.setAttribute("class","edge "+(l.e.relation||"related")); l.el=ln; gE.appendChild(ln); return ln; });
 var nodeEls=nodes.map(function(o){ var g=el("g",{}); g.setAttribute("class","node"); g.setAttribute("data-id",o.d.id);
   var c=el("circle",{r:radius(o),fill:color(o.d)}); g.appendChild(c);
   var tx=el("text",{x:radius(o)+3,y:4}); tx.textContent=o.d.label||o.d.id; g.appendChild(tx);
   o.el=g; o.circle=c; gN.appendChild(g); return g; });

 var tf={k:1,x:0,y:0};
 function applyTf(){ view.setAttribute("transform","translate("+tf.x+","+tf.y+") scale("+tf.k+")"); }
 function render(){ links.forEach(function(l){ l.el.setAttribute("x1",l.s.x);l.el.setAttribute("y1",l.s.y);l.el.setAttribute("x2",l.t.x);l.el.setAttribute("y2",l.t.y); });
   nodes.forEach(function(o){ o.el.setAttribute("transform","translate("+o.x+","+o.y+")"); }); }
 // 初始居中
 (function(){ var minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9; nodes.forEach(function(o){ if(o.x<minx)minx=o.x;if(o.y<miny)miny=o.y;if(o.x>maxx)maxx=o.x;if(o.y>maxy)maxy=o.y; });
   var bw=Math.max(1,maxx-minx),bh=Math.max(1,maxy-miny); tf.k=Math.min(1.1,0.9*Math.min(W/bw,H/bh)); if(!isFinite(tf.k)||tf.k<=0)tf.k=1;
   tf.x=W/2-(minx+maxx)/2*tf.k; tf.y=H/2-(miny+maxy)/2*tf.k; applyTf(); })();
 render();

 // 连续低能耗仿真（拖拽时回温）
 var alpha=0.0;
 function loop(){ if(alpha>0.005){ tick(alpha); alpha*=0.96; render(); } requestAnimationFrame(loop); }
 requestAnimationFrame(loop);
 function reheat(){ alpha=Math.max(alpha,0.6); }

 function esc(x){ return String(x==null?"":x).split("&").join("&amp;").split("<").join("&lt;").split(">").join("&gt;"); }
 function obsURI(d){ var p=(d.path||""); return "obsidian://open?path="+encodeURIComponent((VAULT?VAULT+"/":"")+p); }
 function setLit(id){ nodes.forEach(function(o){ var on=!id||o.d.id===id||(nb[id]&&nb[id][o.d.id]);
    o.el.setAttribute("class","node"+(id&&!on?" faded":(id&&o.d.id===id?" lit":""))); });
   links.forEach(function(l){ var on=!id||l.s.d.id===id||l.t.d.id===id;
    l.el.setAttribute("class","edge "+(l.e.relation||"related")+(id?(on?" lit":" faded"):"")); }); }
 function clearLit(){ nodes.forEach(function(o){ o.el.setAttribute("class","node"); });
   links.forEach(function(l){ l.el.setAttribute("class","edge "+(l.e.relation||"related")); }); }

 var selected=null;
 function select(o){ selected=o; setLit(o.d.id); var d=o.d;
   var refs=(d.source_refs||[]).map(function(r){ return (typeof r==="string")?r:(r.source||""); }).filter(Boolean).join(", ");
   var n=(deg[d.id]||0);
   document.getElementById("detail").className="";
   document.getElementById("detail").innerHTML=
     "<div class='ttl'>"+esc(d.label||d.id)+"</div>"+
     "<div class='muted'>"+esc(d.type)+" · "+esc(commLabel[d.community_id]||d.community_id||"")+" · "+n+" 条关系</div>"+
     "<a class='obs' href='"+obsURI(d)+"'>在 Obsidian 中打开 ▸</a>"+
     "<div class='pathline'>"+esc(d.path||"")+"</div>"+
     (d.summary?("<p>"+esc(d.summary)+"</p>"):"");
 }

 nodeEls.forEach(function(g,i){ var o=nodes[i];
   g.addEventListener("mouseenter",function(){ if(!selected)setLit(o.d.id); });
   g.addEventListener("mouseleave",function(){ if(!selected)clearLit(); });
   g.addEventListener("dblclick",function(ev){ ev.stopPropagation(); window.location.href=obsURI(o.d); });
   g.addEventListener("mousedown",function(ev){ ev.stopPropagation(); drag={o:o}; o.fixed=true; reheat();
     ev.preventDefault(); });
   g.addEventListener("click",function(ev){ ev.stopPropagation(); if(!moved) select(o); });
 });

 // 拖拽节点 / 平移 / 缩放
 var drag=null, pan=null, moved=false;
 function toGraph(mx,my){ return {x:(mx-tf.x)/tf.k, y:(my-tf.y)/tf.k}; }
 svg.addEventListener("mousedown",function(ev){ pan={x:ev.clientX,y:ev.clientY,ox:tf.x,oy:tf.y}; moved=false; svg.classList.add("panning"); });
 window.addEventListener("mousemove",function(ev){ moved=true;
   if(drag){ var rect=svg.getBoundingClientRect(); var p=toGraph(ev.clientX-rect.left,ev.clientY-rect.top); drag.o.x=p.x; drag.o.y=p.y; drag.o.fixed=true; reheat(); render(); return; }
   if(pan){ tf.x=pan.ox+(ev.clientX-pan.x); tf.y=pan.oy+(ev.clientY-pan.y); applyTf(); } });
 window.addEventListener("mouseup",function(){ drag=null; pan=null; svg.classList.remove("panning"); });
 svg.addEventListener("click",function(){ if(!moved){ selected=null; clearLit(); } });
 svg.addEventListener("wheel",function(ev){ ev.preventDefault(); var rect=svg.getBoundingClientRect();
   var mx=ev.clientX-rect.left, my=ev.clientY-rect.top; var p=toGraph(mx,my);
   var f=ev.deltaY<0?1.12:1/1.12; tf.k=Math.max(0.1,Math.min(6,tf.k*f));
   tf.x=mx-p.x*tf.k; tf.y=my-p.y*tf.k; applyTf(); },{passive:false});

 // 控件
 var sel=document.getElementById("community-filter"), legend=document.getElementById("comm-legend");
 (raw.communities||[]).slice().sort(function(a,b){return (a.label||a.id)<(b.label||b.id)?-1:1;}).forEach(function(c){
   var o=document.createElement("option"); o.value=c.id; o.textContent=(c.label||c.id); sel.appendChild(o);
   var row=document.createElement("div"); row.innerHTML="<span class='sw' style='background:"+color({community_id:c.id})+"'></span>"+esc(c.label||c.id); legend.appendChild(row); });
 sel.addEventListener("change",function(){ var v=sel.value; selected=null;
   nodes.forEach(function(o){ o.el.setAttribute("class","node"+(v&&o.d.community_id!==v?" faded":"")); });
   links.forEach(function(l){ var on=!v||(l.s.d.community_id===v&&l.t.d.community_id===v); l.el.setAttribute("class","edge "+(l.e.relation||"related")+(v&&!on?" faded":"")); }); });
 document.getElementById("search").addEventListener("input",function(ev){ var q=(ev.target.value||"").toLowerCase(); selected=null;
   nodes.forEach(function(o){ var hay=((o.d.label||"")+" "+(o.d.path||"")+" "+((o.d.aliases||[]).join(" "))).toLowerCase();
     o.el.setAttribute("class","node"+(q&&hay.indexOf(q)<0?" faded":"")); }); });
 var pathSet={}; (raw.learning_paths||[]).forEach(function(p){ (p.node_ids||[]).forEach(function(i){ pathSet[i]=1; }); });
 document.getElementById("learning-path").addEventListener("click",function(){ var has=Object.keys(pathSet).length>0; selected=null;
   nodes.forEach(function(o){ o.el.setAttribute("class","node"+(has&&!pathSet[o.d.id]?" faded":"")); }); });
 document.getElementById("reset").addEventListener("click",function(){ selected=null; clearLit();
   nodes.forEach(function(o){ o.fixed=false; }); reheat();
   var minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9; nodes.forEach(function(o){ if(o.x<minx)minx=o.x;if(o.y<miny)miny=o.y;if(o.x>maxx)maxx=o.x;if(o.y>maxy)maxy=o.y; });
   var bw=Math.max(1,maxx-minx),bh=Math.max(1,maxy-miny); tf.k=Math.min(1.1,0.9*Math.min(W/bw,H/bh)); if(!isFinite(tf.k)||tf.k<=0)tf.k=1;
   tf.x=W/2-(minx+maxx)/2*tf.k; tf.y=H/2-(miny+maxy)/2*tf.k; applyTf(); });
})();
</script>
</body>
</html>
"""


def to_html(data: dict, vault_root: str = "") -> str:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    degraded = len(nodes) > MAX_HTML_NODES or len(edges) > MAX_HTML_EDGES
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    vault_js = json.dumps(vault_root or "").replace("</", "<\\/")
    banner = ('<div class="banner" id="degraded-banner">图规模较大，已进入降级模式：'
              '默认只显示社区代表节点与学习路径。</div>') if degraded else ""
    html = _TEMPLATE
    html = html.replace("__DEGRADED_BANNER__", banner)
    html = html.replace("__DEGRADED_FLAG__", "true" if degraded else "false")
    html = html.replace("__VAULT_ROOT_JSON__", vault_js)
    html = html.replace("__PAYLOAD__", payload)
    return html


def write_html(vault, data: dict) -> Path:
    vault = Path(vault)
    out = vault / HTML_FILE
    out.write_text(to_html(data, vault_root=vault.resolve().as_posix()),
                   encoding="utf-8", newline="\n")
    return out
