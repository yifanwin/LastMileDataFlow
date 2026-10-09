#!/usr/bin/env python3
"""从真实的 Scene Graph 产物生成一份自包含的交互式 HTML 可视化。

数据源（真实运行 construct-29365f8d6330，val_103）：
  - contexts/round_000/initialization/baseline/graph.json   （全图）
  - contexts/round_000/local_graph.json                     （给 Agent 的局部图）

输出：reports/近期实验结果报告/scene-graph.html

设计（遵循 data-viz 规范）：
  - 平面投影视图（世界 XY），把节点画在真实位置上；region 面按 axes/bounds 画成四边形；
  - 四类节点用通过校验的分类色板（--pairs all 通过；aqua 对比度 <3:1 → 用图例文字标签兜底）；
  - 边默认只画物理有据的 supported_by，part_of / has_region 可开关；
  - 图例常驻 + 节点表（table view）作为 accessibility 兜底。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs" / "case_construction" / "construct-29365f8d6330"
FULL = RUN / "contexts" / "round_000" / "initialization" / "baseline" / "graph.json"
LOCAL = RUN / "contexts" / "round_000" / "local_graph.json"
OUT = ROOT / "reports" / "近期实验结果报告" / "scene-graph.html"


def r3(v):
    return [round(float(x), 3) for x in v]


def trim_node(nid: str, n: dict) -> dict:
    """只保留可视化需要的字段，控制 HTML 体积。"""
    kind = n.get("kind")
    if kind == "region":
        return {
            "i": nid, "k": "region",
            "o": r3(n["origin"]),
            "ax": [r3(a) for a in n["axes"]],
            "b": r3(n["bounds"]),
            "s": n.get("support"),
            "ev": n.get("evidence"),
        }
    p = n.get("pose") or {}
    out = {
        "i": nid,
        "k": kind,
        "cat": n.get("category") or n.get("region_kind"),
        "p": r3(p["position"]) if p.get("position") else None,
        "ph": n.get("parent_hint"),
        "sc": n.get("support_capability"),
        "st": n.get("support_status"),
        "cc": n.get("construction_capabilities"),
        "room": n.get("room_id"),
        "away": n.get("anchor"),
        "man": n.get("manipulable"),
        "rm": n.get("root_motion"),
    }
    bw = n.get("collision_bounds_world")
    if bw:
        out["bw"] = [r3(b) for b in bw]
    fp = n.get("footprint_world")
    if fp:
        out["fp"] = [r3(t) for t in fp]
    return out


def load(path: Path):
    d = json.loads(path.read_text())
    g = d if "nodes" in d else d["graph"]
    nodes = {nid: trim_node(nid, n) for nid, n in g["nodes"].items()}
    meta = {k: d.get(k) for k in
            ("graph_id", "graph_version", "scene_id", "revision", "stage",
             "time_s", "units", "pose_order") if k in d}
    return meta, nodes, g["edges"], g.get("issues", [])


def main() -> None:
    fmeta, fnodes, fedges, fissues = load(FULL)
    lmeta, lnodes, ledges, _ = load(LOCAL)
    local_ids = sorted(lnodes)

    payload = {
        "meta": fmeta,
        "nodes": fnodes,
        "edges": fedges,
        "issues": fissues,
        "localIds": local_ids,
        "localMeta": {"scope": json.loads(LOCAL.read_text()).get("scope"),
                      "radius_m": json.loads(LOCAL.read_text()).get("radius_m"),
                      "target": json.loads(LOCAL.read_text()).get("target"),
                      "support": json.loads(LOCAL.read_text()).get("support")},
    }
    OUT.write_text(HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                                       separators=(",", ":"))),
                   encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  full nodes={len(fnodes)} edges={len(fedges)} issues={len(fissues)}")
    print(f"  local nodes={len(lnodes)} edges={len(ledges)}")


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Scene Graph 可视化 — construct-29365f8d6330 (val_103)</title>
<style>
  :root{
    color-scheme: light;
    --surface-1:#fcfcfb; --page:#f9f9f7;
    --ink-1:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
    --c-object:#2a78d6; --c-part:#1baf7a; --c-region:#4a3aa7; --c-station:#eb6834;
  }
  @media (prefers-color-scheme: dark){
    :root {
      color-scheme: dark;
      --surface-1:#1a1a19; --page:#0d0d0d;
      --ink-1:#fff; --ink-2:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
      --c-object:#3987e5; --c-part:#199e70; --c-region:#9085e9; --c-station:#d95926;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--page);color:var(--ink-1);
       font:14px/1.5 system-ui,-apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
  header{padding:14px 18px;border-bottom:1px solid var(--border)}
  h1{margin:0 0 4px;font-size:17px}
  .sub{color:var(--ink-2);font-size:12.5px}
  .sub code{background:var(--surface-1);border:1px solid var(--border);
            border-radius:4px;padding:1px 5px;font-size:12px}
  .wrap{display:grid;grid-template-columns:1fr 330px;gap:0;align-items:start}
  .left{padding:12px 14px}
  .right{border-left:1px solid var(--border);padding:12px 14px;min-height:70vh}
  .bar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;
       padding:8px 10px;margin-bottom:10px;background:var(--surface-1);
       border:1px solid var(--border);border-radius:8px}
  .grp{display:flex;gap:10px;align-items:center}
  .grp>.lbl{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
  label.tog{display:inline-flex;gap:5px;align-items:center;cursor:pointer;font-size:12.5px;
            color:var(--ink-2);user-select:none}
  .dot{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none}
  .dot.object{background:var(--c-object)} .dot.part{background:var(--c-part)}
  .dot.region{background:var(--c-region)} .dot.station{background:var(--c-station)}
  svg{width:100%;height:auto;display:block;background:var(--surface-1);
      border:1px solid var(--border);border-radius:8px}
  .node{cursor:pointer}
  .node.sel{stroke:var(--ink-1);stroke-width:2.5}
  .dim{opacity:.16}
  .tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--ink-1);
       border:1px solid var(--border);border-radius:6px;padding:6px 9px;font-size:12px;
       box-shadow:0 4px 14px rgba(0,0,0,.14);max-width:330px;opacity:0;transition:opacity .08s}
  h2{font-size:13px;margin:14px 0 6px;color:var(--ink-1)}
  h2:first-child{margin-top:0}
  table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{text-align:left;padding:3px 5px;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600;font-size:11px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .kv{display:grid;grid-template-columns:96px 1fr;gap:2px 8px;font-size:12px}
  .kv dt{color:var(--muted)} .kv dd{margin:0;word-break:break-all}
  .note{color:var(--ink-2);font-size:12px}
  .note b{color:var(--ink-1)}
  .legend{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--ink-2)}
  .legend div{display:flex;gap:7px;align-items:flex-start}
  .scrolled{max-height:230px;overflow:auto}
</style>
</head>
<body>
<header>
  <h1>Scene Graph 可视化</h1>
  <div class="sub" id="sub"></div>
</header>

<div class="wrap">
  <div class="left">
    <div class="bar">
      <div class="grp">
        <span class="lbl">视图</span>
        <label class="tog"><input type="radio" name="view" value="full" checked>全图</label>
        <label class="tog"><input type="radio" name="view" value="local">Agent 局部图</label>
      </div>
      <div class="grp">
        <span class="lbl">节点</span>
        <label class="tog"><input type="checkbox" id="t-object" checked><span class="dot object"></span>object</label>
        <label class="tog"><input type="checkbox" id="t-part"><span class="dot part"></span>part</label>
        <label class="tog"><input type="checkbox" id="t-region" checked><span class="dot region"></span>region</label>
        <label class="tog"><input type="checkbox" id="t-station" checked><span class="dot station"></span>station</label>
      </div>
      <div class="grp">
        <span class="lbl">边</span>
        <label class="tog"><input type="checkbox" id="e-sup" checked>supported_by</label>
        <label class="tog"><input type="checkbox" id="e-part">part_of</label>
        <label class="tog"><input type="checkbox" id="e-reg">has_region</label>
      </div>
    </div>
    <svg id="svg" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid meet"></svg>
    <p class="note" style="margin:10px 2px 0">
      俯视投影：节点画在<b>实测世界 XY 位置</b>，region 面按自身的 <code>axes/bounds</code> 画成四边形。
      颜色只编码节点类型，<b>不编码成功与否</b>。鼠标悬停看详情，点击锁定。
    </p>
  </div>

  <div class="right">
    <h2>详情</h2>
    <div id="detail" class="note">点击图中任一节点查看字段。</div>

    <h2>图例</h2>
    <div class="legend">
      <div><span class="dot object" style="margin-top:4px"></span><span><b>object</b>：物体/家具实例（画碰撞足迹多边形）</span></div>
      <div><span class="dot part" style="margin-top:4px"></span><span><b>part</b>：模型子刚体（按 model_tree 拆分）</span></div>
      <div><span class="dot region" style="margin-top:4px"></span><span><b>region</b>：可放置水平面（碰撞几何顶面）</span></div>
      <div><span class="dot station" style="margin-top:4px"></span><span><b>station</b>：机器人基座参考</span></div>
      <div><span style="display:inline-block;width:18px;border-top:2px solid var(--c-region);margin-top:7px"></span><span><b>supported_by</b>：实测到承力接触的支撑关系（唯一有物理依据的边）</span></div>
    </div>

    <h2>规模</h2>
    <table id="stats"></table>

    <h2>issues（明确记录的未知）</h2>
    <div id="issues" class="note"></div>

    <h2>节点表（table view）</h2>
    <div class="scrolled"><table id="table"></table></div>
  </div>
</div>

<div class="tip" id="tip"></div>

<script>
const D = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const COLOR = {object:"var(--c-object)", part:"var(--c-part)",
               region:"var(--c-region)", station:"var(--c-station)"};
const LABEL = {object:"categorical slot 1", part:"slot 3", region:"slot 7", station:"slot 2"};

const N = D.nodes, E = D.edges;
const LOCAL = new Set(D.localIds);

/* ---- 计算世界坐标范围（只按有位置的点，忽略远处离群） ---- */
function pts(){
  const a = [];
  for (const id in N){
    const n = N[id];
    if (n.p) a.push([n.p[0], n.p[1]]);
    if (n.bw) a.push([n.bw[0][0], n.bw[0][1]], [n.bw[1][0], n.bw[1][1]]);
    if (n.k === "region") a.push([n.o[0], n.o[1]]);
  }
  return a;
}
const P = pts();
const xs = P.map(p=>p[0]), ys = P.map(p=>p[1]);
let X0=Math.min(...xs), X1=Math.max(...xs), Y0=Math.min(...ys), Y1=Math.max(...ys);
const pad = 0.25;
X0-=pad; X1+=pad; Y0-=pad; Y1+=pad;
const W = 900, H = 560;
const sc = Math.min((W-40)/(X1-X0), (H-40)/(Y1-Y0));
const ox = (W - (X1-X0)*sc)/2, oy = (H - (Y1-Y0)*sc)/2;
function mx(x){ return ox + (x - X0)*sc; }
function my(y){ return H - oy - (y - Y0)*sc; }   // 世界 +y 朝上

const svg = document.getElementById("svg");
const tip = document.getElementById("tip");

/* ---- 状态 ---- */
const st = {view:"full", types:{object:true, part:false, region:true, station:true},
            edges:{sup:true, part:false, reg:false}, sel:null};

function visible(id){
  const n = N[id];
  if (!st.types[n.k]) return false;
  if (st.view === "local" && !LOCAL.has(id)) return false;
  return true;
}

/* ---- 渲染 ---- */
function render(){
  svg.innerHTML = "";
  const gGrid = document.createElementNS(NS, "g");

  // 网格
  for (let gx = Math.ceil(X0); gx <= X1; gx++){
    const l = document.createElementNS(NS,"line");
    l.setAttribute("x1",mx(gx)); l.setAttribute("x2",mx(gx));
    l.setAttribute("y1",my(Y0)); l.setAttribute("y2",my(Y1));
    l.setAttribute("stroke","var(--grid)"); l.setAttribute("stroke-width",1);
    gGrid.appendChild(l);
  }
  for (let gy = Math.ceil(Y0); gy <= Y1; gy++){
    const l = document.createElementNS(NS,"line");
    l.setAttribute("y1",my(gy)); l.setAttribute("y2",my(gy));
    l.setAttribute("x1",mx(X0)); l.setAttribute("x2",mx(X1));
    l.setAttribute("stroke","var(--grid)"); l.setAttribute("stroke-width",1);
    gGrid.appendChild(l);
  }
  svg.appendChild(gGrid);

  // 1) region 面（最底层）
  const gReg = document.createElementNS(NS,"g");
  for (const id in N){
    const n = N[id];
    if (n.k !== "region" || !visible(id)) continue;
    const [u,v] = n.ax, o = n.o, b = n.b;
    const corners = [[b[0],b[2]],[b[1],b[2]],[b[1],b[3]],[b[0],b[3]]].map(([s,t])=>
      [o[0]+s*u[0]+t*v[0], o[1]+s*u[1]+t*v[1]]);
    const el = document.createElementNS(NS,"polygon");
    el.setAttribute("points", corners.map(([x,y])=>`${mx(x)},${my(y)}`).join(" "));
    el.setAttribute("fill", COLOR.region);
    el.setAttribute("fill-opacity", st.sel&&st.sel!==id ? 0.04 : 0.16);
    el.setAttribute("stroke", COLOR.region);
    el.setAttribute("stroke-width", 1);
    el.setAttribute("class","node" + (st.sel===id?" sel":"") + (st.sel&&st.sel!==id?" dim":""));
    el.dataset.id = id;
    gReg.appendChild(el);
  }
  svg.appendChild(gReg);

  // 2) 边
  const gE = document.createElementNS(NS,"g");
  const draw = (pred, on, color, width, dash) => {
    if (!on) return;
    for (const e of E){
      if (e.predicate !== pred) continue;
      const [a, b2] = e.args;
      if (!visible(a) || !visible(b2)) continue;
      const A = N[a], B = N[b2];
      const pa = A.p ? [A.p[0],A.p[1]] : (A.k==="region"?[A.o[0],A.o[1]]:null);
      const pb = B.p ? [B.p[0],B.p[1]] : (B.k==="region"?[B.o[0],B.o[1]]:null);
      if (!pa || !pb) continue;
      const l = document.createElementNS(NS,"line");
      l.setAttribute("x1",mx(pa[0])); l.setAttribute("y1",my(pa[1]));
      l.setAttribute("x2",mx(pb[0])); l.setAttribute("y2",my(pb[1]));
      l.setAttribute("stroke", color); l.setAttribute("stroke-width", width);
      l.setAttribute("stroke-opacity", .55);
      if (dash) l.setAttribute("stroke-dasharray", dash);
      l.setAttribute("class", st.sel && st.sel!==a && st.sel!==b2 ? "dim" : "");
      gE.appendChild(l);
    }
  };
  draw("supported_by", st.edges.sup, "var(--c-region)", 1.6);
  draw("part_of",      st.edges.part, "var(--c-part)", 1, "3 3");
  draw("has_region",   st.edges.reg,  "var(--muted)", 0.8, "1 3");
  svg.appendChild(gE);

  // 3) 节点
  const gN = document.createElementNS(NS,"g");
  const ordered = Object.keys(N).sort((a,b)=>rank(N[a].k)-rank(N[b].k));
  for (const id of ordered){
    const n = N[id];
    if (!visible(id)) continue;
    let el;
    if (n.k === "region") continue;         // 已画
    if (n.k === "station"){
      el = document.createElementNS(NS,"circle");
      el.setAttribute("cx",mx(n.p[0])); el.setAttribute("cy",my(n.p[1]));
      el.setAttribute("r",9);
      el.setAttribute("fill", COLOR.station);
      el.setAttribute("stroke","var(--surface-1)"); el.setAttribute("stroke-width",2.5);
    } else if (n.fp && n.fp.length >= 3){
      el = document.createElementNS(NS,"polygon");
      el.setAttribute("points", n.fp.map(([x,y])=>`${mx(x)},${my(y)}`).join(" "));
      el.setAttribute("fill", COLOR[n.k]);
      el.setAttribute("fill-opacity", .34);
      el.setAttribute("stroke", COLOR[n.k]);
      el.setAttribute("stroke-width", 1.6);
    } else {
      el = document.createElementNS(NS,"circle");
      el.setAttribute("cx",mx(n.p[0])); el.setAttribute("cy",my(n.p[1]));
      el.setAttribute("r", n.k==="part"?2.6:5);
      el.setAttribute("fill", COLOR[n.k]);
      el.setAttribute("fill-opacity", n.k==="part"?.6:.9);
      el.setAttribute("stroke","var(--surface-1)"); el.setAttribute("stroke-width",1.4);
    }
    el.setAttribute("class","node" + (st.sel===id?" sel":"") + (st.sel&&st.sel!==id?" dim":""));
    el.dataset.id = id;
    gN.appendChild(el);
  }
  svg.appendChild(gN);

  // 目标/支撑 标注
  if (D.localMeta && D.localMeta.target && visible(D.localMeta.target)){
    const t = N[D.localMeta.target];
    if (t && t.p){
      const tx = document.createElementNS(NS,"text");
      tx.setAttribute("x", mx(t.p[0])+10); tx.setAttribute("y", my(t.p[1])-8);
      tx.setAttribute("font-size",11); tx.setAttribute("fill","var(--ink-1)");
      tx.textContent = "target";
      svg.appendChild(tx);
    }
  }
  const sp = N["station_start"];
  if (sp && visible("station_start")){
    const tx = document.createElementNS(NS,"text");
    tx.setAttribute("x", mx(sp.p[0])+12); tx.setAttribute("y", my(sp.p[1])+4);
    tx.setAttribute("font-size",11); tx.setAttribute("fill","var(--c-station)");
    tx.textContent = "station_start";
    svg.appendChild(tx);
  }
}
function rank(k){ return {region:0, part:1, object:2, station:3}[k] ?? 4; }

/* ---- 交互 ---- */
svg.addEventListener("mousemove", ev => {
  const id = ev.target.dataset && ev.target.dataset.id;
  if (!id){ tip.style.opacity = 0; return; }
  tip.innerHTML = summary(id);
  tip.style.left = (ev.clientX + 14) + "px";
  tip.style.top  = (ev.clientY + 14) + "px";
  tip.style.opacity = 1;
});
svg.addEventListener("mouseleave", ()=> tip.style.opacity = 0);
svg.addEventListener("click", ev => {
  const id = ev.target.dataset && ev.target.dataset.id;
  st.sel = (id && id !== st.sel) ? id : null;
  render(); showDetail(st.sel);
});

function summary(id){
  const n = N[id];
  if (n.k === "region") return `<b>region</b><br>${id}<br>支撑物 ${n.s}`;
  return `<b>${n.k}</b> ${n.cat ? "· " + n.cat : ""}<br>${id}` +
         (n.p ? `<br>[${n.p.join(", ")}]` : "");
}

function showDetail(id){
  const el = document.getElementById("detail");
  if (!id){ el.textContent = "点击图中任一节点查看字段。"; return; }
  const n = N[id];
  const rows = [];
  const push = (k,v) => { if (v !== undefined && v !== null && v !== "") rows.push(`<dt>${k}</dt><dd>${v}</dd>`); };
  push("type", n.k);
  push("id", id);
  push("类别", n.cat);
  if (n.p) push("坐标 (x,y,z)", n.p.join(", "));
  if (n.k === "region"){
    push("origin", n.o.join(", "));
    push("bounds", n.b.join(", "));
    push("evidence", n.ev);
    push("支撑物", n.s);
  } else {
    push("room_id", n.room);
    push("root_motion", n.rm);
    push("manipulable", n.man);
    push("support_capability", n.sc);
    push("support_status", n.st);
    push("可编辑能力", (n.cc||[]).join(", "));
    push("parent_hint", n.ph ? n.ph + "（<b>仅为线索，非支撑边</b>）" : null);
  }
  const inLocal = LOCAL.has(id) ? "是" : "否";
  push("在 Agent 局部图内", inLocal);
  const ins = E.filter(e=>e.args.includes(id));
  const counts = {};
  ins.forEach(e => counts[e.predicate] = (counts[e.predicate]||0)+1);
  push("关联边", Object.entries(counts).map(([k,v])=>`${k} ×${v}`).join("，") || "无");
  el.innerHTML = `<dl class="kv">${rows.join("")}</dl>`;
}

/* ---- 侧栏 ---- */
function buildStats(){
  const kinds = {}, preds = {};
  for (const id in N) kinds[N[id].k] = (kinds[N[id].k]||0)+1;
  for (const e of E) preds[e.predicate] = (preds[e.predicate]||0)+1;
  const rows = [["节点总数", Object.keys(N).length],
                ["object", kinds.object||0], ["part", kinds.part||0],
                ["region", kinds.region||0], ["station", kinds.station||0],
                ["边总数", E.length],
                ["supported_by", preds.supported_by||0],
                ["has_region", preds.has_region||0],
                ["part_of", preds.part_of||0],
                ["issues", D.issues.length],
                ["Agent 局部图节点", D.localIds.length]];
  document.getElementById("stats").innerHTML =
    rows.map(([k,v])=>`<tr><td>${k}</td><td class="num">${v}</td></tr>`).join("");
  document.getElementById("issues").innerHTML =
    D.issues.map(i=>`<div>· <code>${i.code}</code><br>&nbsp;&nbsp;${i.instance}：${i.reason}</div>`).join("")
    || "无";

  const tv = Object.keys(N).sort().slice(0, 400).map(id=>{
    const n = N[id];
    return `<tr><td>${n.k}</td><td>${n.cat||""}</td><td>${id.slice(0,28)}</td></tr>`;
  }).join("");
  document.getElementById("table").innerHTML =
    `<tr><th>type</th><th>类别</th><th>id</th></tr>${tv}`;

  const m = D.meta, lm = D.localMeta;
  document.getElementById("sub").innerHTML =
    `运行 <code>construct-29365f8d6330</code> · 场景 <code>${m.scene_id}</code> · ` +
    `stage=<code>${m.stage}</code> · revision=${m.revision} · time=${(+m.time_s).toFixed(2)}s · ` +
    `units=${m.units} · pose=${m.pose_order}。` +
    `局部图 radius=${lm.radius_m}m，scope=<code>${lm.scope}</code>。`;
}

/* ---- 绑定 ---- */
document.querySelectorAll('input[name="view"]').forEach(r =>
  r.addEventListener("change", () => { st.view = r.value; render(); }));
[["t-object","object"],["t-part","part"],["t-region","region"],["t-station","station"]]
  .forEach(([id,k]) => document.getElementById(id)
    .addEventListener("change", e => { st.types[k] = e.target.checked; render(); }));
[["e-sup","sup"],["e-part","part"],["e-reg","reg"]].forEach(([id,k]) =>
  document.getElementById(id)
    .addEventListener("change", e => { st.edges[k] = e.target.checked; render(); }));

buildStats();
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
