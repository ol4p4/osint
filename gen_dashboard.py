#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_FILE = Path(r"D:\osint\data\dashboard_data.json")
HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")
HTML_FILE = Path(r"D:\osint\data\interactive_dashboard.html")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

intel = data.get("intelligence", [])

hyps = []
try:
    with open(HYP_FILE, "r", encoding="utf-8") as f:
        hyps = json.load(f)
except:
    pass

by_id = {h["id"]: h for h in hyps}
major_ids = [h["id"] for h in hyps if h.get("level") == "major"]
major_ids.sort(key=lambda x: by_id.get(x, {}).get("confidence", 0), reverse=True)

h_json = json.dumps(hyps, ensure_ascii=False)
m_json = json.dumps(major_ids, ensure_ascii=False)
i_json = json.dumps(intel, ensure_ascii=False)

now = datetime.now(timezone(timedelta(hours=8))).isoformat()

# Build HTML as list of parts
parts = []
parts.append('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>参谋系统</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0d1117;color:#c9d1d9}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;max-width:1400px;margin:0 auto}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.panel{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;max-height:92vh;overflow-y:auto}
h2{font-size:16px;margin-bottom:12px;color:#58a6ff}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.btn{padding:4px 10px;border:1px solid #30363d;border-radius:12px;background:#21262d;color:#c9d1d9;cursor:pointer;font-size:12px;transition:all .2s}
.btn:hover,.btn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
.sort-btns{display:flex;gap:6px;margin-bottom:12px}
.item{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:8px;transition:transform .15s}
.item:hover{transform:translateX(4px);border-color:#58a6ff}
.meta-row{display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap;align-items:center}
.cat{font-size:10px;padding:2px 6px;border-radius:8px;font-weight:600}
.cat-macro,.cat-macroeconomics{background:#a371f722;color:#a371f7}
.cat-finance,.cat-金融市场{background:#f0883e22;color:#f0883e}
.cat-geopolitics,.cat-地缘政治{background:#f8514922;color:#f85149}
.cat-energy,.cat-能源安全{background:#3fb95022;color:#3fb950}
.cat-east_asia,.cat-东亚,.cat-东亚政治经济{background:#d2a8ff22;color:#d2a8ff}
.cat-trade,.cat-贸易,.cat-贸易政策{background:#79c0ff22;color:#79c0ff}
.cat-tech,.cat-科技,.cat-科技产业{background:#1f6feb22;color:#58a6ff}
.cat-social,.cat-社会{background:#ffa65722;color:#ffa657}
.cat-other{background:#484f58;color:#8b949e}
.badge{font-size:10px;padding:1px 5px;border-radius:6px;background:#30363d;color:#8b949e}
.rel{font-size:10px;padding:1px 5px;border-radius:6px;margin-left:auto}
.rel-5{background:#f8514933;color:#f85149}
.rel-4{background:#f0883e33;color:#f0883e}
.rel-3{background:#3fb95033;color:#3fb950}
.src{font-size:10px;color:#8b949e}
.time-tag{font-size:10px;color:#484f58;background:#21262d;padding:1px 6px;border-radius:6px}
.title{font-size:14px;font-weight:600;color:#e6edf3;margin-bottom:4px}
.body{font-size:12px;color:#8b949e;line-height:1.6}
.impact{font-size:11px;color:#3fb950;margin-top:6px;padding:4px 8px;background:rgba(63,159,80,.1);border-radius:4px}
.kf{font-size:11px;color:#79c0ff;margin-top:4px;padding:4px 8px;background:rgba(121,192,255,.08);border-radius:4px}
.expand-btn{font-size:10px;padding:2px 8px;border:1px solid #30363d;border-radius:4px;background:#21262d;color:#8b949e;cursor:pointer;margin-top:6px}
.expand-btn:hover{background:#30363d;color:#c9d1d9}
.orig{display:none;font-size:11px;color:#6a737d;margin-top:8px;padding:8px;background:#0d1117;border:1px solid #30363d;border-radius:4px;white-space:pre-wrap;max-height:200px;overflow-y:auto}
.strategy-shell{max-width:1400px;margin:14px auto 0;padding:20px;background:linear-gradient(135deg,#111827,#161b22 45%,#101828);border:1px solid #388bfd66;border-radius:12px;box-shadow:0 0 32px #1f6feb22}
.strategy-head{display:flex;gap:16px;align-items:center;justify-content:space-between;margin-bottom:16px}
.strategy-kicker{color:#79c0ff;font-size:12px;font-weight:700}
.strategy-shell h1{font-size:26px;color:#f0f6fb;margin:5px 0}
.strategy-shell p{font-size:12px;color:#8b949e}
#hypSearch{width:min(420px,42vw);padding:10px 12px;border:1px solid #30363d;border-radius:8px;background:#0d1117;color:#c9d1d9}
.major-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.major-card,.medium-card,.small-card{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:14px;text-align:left;color:#c9d1d9}
.major-card{cursor:pointer;border-color:#388bfd55;transition:.15s}
.major-card:hover{transform:translateY(-2px);border-color:#58a6ff;box-shadow:0 8px 24px #1f6feb33}
.major-card h3{min-height:44px;font-size:16px;line-height:1.35;margin:9px 0;color:#f0f6fb}
.major-card p,.medium-card p,.small-card p{min-height:34px;font-size:11px;color:#8b949e;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-top{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#8b949e}
.level-pill{padding:2px 7px;border-radius:99px;background:#f8514922;color:#ff7b72;font-weight:800}
.level-pill.medium{background:#f0883e22;color:#f0883e}
.level-pill.small{background:#58a6ff22;color:#79c0ff}
.card-foot{display:flex;justify-content:space-between;gap:6px;margin-top:9px;font-size:10px;color:#8b949e}
.progress{position:relative;height:7px;margin-top:10px;background:#21262d;border-radius:99px;overflow:hidden}
.progress span{display:block;height:100%;border-radius:99px}
.progress b{position:absolute;right:7px;top:-1px;font-size:9px;color:#f0f6fb;text-shadow:0 1px 2px #000}
.progress-large{height:12px}
.progress-large b{top:-1px;font-size:10px}
.hyp-modal{position:fixed;inset:0;z-index:999;display:none;padding:24px;background:#010409e6}
.hyp-modal.open{display:flex;align-items:center;justify-content:center}
.hyp-modal-box{width:min(1300px,97vw);height:min(900px,94vh);overflow:hidden;background:#0d1117;border:1px solid #30363d;border-radius:12px}
.hyp-modal-top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid #30363d;background:#161b22}
.breadcrumb{display:flex;align-items:center;gap:8px;font-size:13px;color:#8b949e;overflow:hidden;white-space:nowrap}
.breadcrumb button{padding:5px 9px;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#c9d1d9;cursor:pointer}
.modal-close{padding:7px 11px;border:0;border-radius:6px;background:#f85149;color:white;cursor:pointer}
.hyp-modal-columns{display:grid;grid-template-columns:minmax(300px,380px) 1fr;height:calc(100% - 59px)}
.modal-pane{padding:16px;overflow:auto}
.branch-pane{border-left:1px solid #30363d;background:#010409}
.pane-title{margin:16px 0 9px;color:#58a6ff;font-size:13px;font-weight:700}
.modal-pane>.pane-title:first-child{margin-top:0}
.medium-card{width:100%;margin-bottom:9px;cursor:pointer;border-left:3px solid #f0883e}
.medium-card:hover{border-color:#f0883e}
.medium-card.selected{border-color:#58a6ff;background:#161b22}
.medium-card h4,.small-card h4{font-size:13px;line-height:1.4;margin:8px 0;color:#f0f6fb}
.back-btn{width:100%;margin-bottom:12px;padding:8px;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#c9d1d9;cursor:pointer}
.detail-hero{padding:14px;border:1px solid #30363d;border-radius:8px;background:#161b22}
.detail-hero h2{font-size:19px;line-height:1.35;margin:8px 0}
.detail-hero p{font-size:12px;line-height:1.55;color:#8b949e}
.meta-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:11px;font-size:10px;color:#8b949e;text-align:center}
.meta-grid b{display:block;margin-top:3px;color:#c9d1d9;font-size:11px}
.small-card{margin-bottom:10px;border-left:3px solid #58a6ff}
.indicator{display:grid;gap:3px;margin-top:9px;padding:9px;border:1px solid #30363d;border-radius:6px;background:#161b22;font-size:10px;color:#8b949e}
.indicator b{color:#79c0ff;font-size:11px}
.indicator .yes{color:#3fb950}
.indicator .no{color:#f85149}
.note{margin-top:9px;padding:8px;border-radius:5px;font-size:10px;line-height:1.45}
.no-note{background:#f8514911;color:#ffa198}
.empty-tip{padding:18px;border:1px dashed #30363d;border-radius:8px;color:#484f58;text-align:center}
@media(max-width:850px){.strategy-head{align-items:stretch;flex-direction:column}#hypSearch{width:100%}.hyp-modal{padding:8px}.hyp-modal-box{height:96vh}.hyp-modal-columns{grid-template-columns:1fr;height:calc(100% - 53px)}.branch-pane{border-left:0;border-top:1px solid #30363d}.meta-grid{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="wrap">
<div class="strategy-shell">
  <div class="strategy-head">
    <div>
      <div class="strategy-kicker">参谋系统 · 战略假设驾驶舱</div>
      <h1>最大假设优先</h1>
      <p>点击大假设查看中假设；点击中假设查看小假设、指标和阈值。</p>
    </div>
    <input id="hypSearch" placeholder="搜索三战、经济危机、城投、AI、台海…" oninput="renderMajors()">
  </div>
  <div id="majorGrid" class="major-grid"></div>
</div>
<div id="hypModal" class="hyp-modal" onclick="if(event.target===this)closeHyp()">
  <div class="hyp-modal-box">
    <div class="hyp-modal-top">
      <div id="hypBreadcrumb" class="breadcrumb"></div>
      <button class="modal-close" onclick="closeHyp()">关闭</button>
    </div>
    <div class="hyp-modal-columns">
      <div id="majorPane" class="modal-pane"></div>
      <div id="branchPane" class="modal-pane branch-pane"><div class="empty-tip">选择一个中假设。</div></div>
    </div>
  </div>
</div>
<div class="panel">
  <h2>📡 情报流 <span style="font-size:11px;color:#8b949e" id="intelCount"></span></h2>
  <div class="sort-btns">
    <button class="btn active" id="sort-relevance" onclick="S('relevance')">按相关度</button>
    <button class="btn" id="sort-time" onclick="S('time')">按时间</button>
  </div>
  <div class="btns" id="catBtns"></div>
  <div id="il"></div>
</div>
</div>
''')

# Write hypothesis JS as plain string concatenation (no f-string issues)
hyp_js_lines = []
hyp_js_lines.append('<script>')
hyp_js_lines.append('var H=' + h_json + ';')
hyp_js_lines.append('var byId={};H.forEach(function(h){byId[h.id]=h});')
hyp_js_lines.append('var orderedMajors=' + m_json + ';')
hyp_js_lines.append('function descendants(id){var h=byId[id];if(!h||!h.children)return[];var r=[];h.children.forEach(function(c){r.push(c);r=r.concat(descendants(c))});return r}')
hyp_js_lines.append('function statusLabel(s){return s==="active"?"\\u{1f7e2}\\u6d3b\\u8dc3":s==="verified"?"\\u2705\\u5df2\\u9a8c\\u8bc1":s==="falsified"?"\\u274c\\u5df2\\u8bc1\\u4f2a":"\\u2b1c\\u5f85\\u5b9a"}')
hyp_js_lines.append('function progress(x,big){var c=x.confidence||0;var col=c>=0.7?"#3fb950":c>=0.4?"#f0883e":"#f85149";var cls=big?"progress-large":"";return \'<div class="progress \'+cls+\'"><span style="width:\'+Math.round(c*100)+\'%;background:\'+col+\'"></span><b>\'+Math.round(c*100)+\'%</b></div>\'}')
hyp_js_lines.append('function indicatorBlock(ind){var v=ind.current_value;var t=ind.threshold_support;var matched=v&&t&&String(v).indexOf(String(t))>-1;var cls=matched?"yes":"no";return \'<div class="indicator"><b>\'+esc(ind.name)+\'</b><span>\\u5f53\\u524d: \'+(v||"\\u672a\\u77e5")+\'</span><span class="\'+cls+\'">\'+(matched?"\\u5df2\\u89e6\\u53d1\\u652f\\u6301":"\\u672a\\u8fbe\\u9608\\u503c")+\'</span><span>\\u6765\\u6e90: \'+esc(ind.source||"\\u672a\\u77e5")+\'</span></div>\'}')

render_majors = """function renderMajors(){var q=document.getElementById('hypSearch').value.trim().toLowerCase();var rows=orderedMajors.map(function(id){return byId[id]}).filter(function(x){return !q||(x.title+' '+x.rationale+' '+x.direction).toLowerCase().includes(q)});document.getElementById('majorGrid').innerHTML=rows.map(function(x){var n=descendants(x.id).length;return '<button class="major-card" onclick="openMajor(\\''+x.id+'\\')"><div class="card-top"><span class="level-pill">大假设</span><span>'+statusLabel(x.status)+'</span></div><h3>'+esc(x.title)+'</h3><p>'+esc(x.rationale)+'</p>'+progress(x,true)+'<div class="card-foot"><span>'+esc(x.direction||"方向未标")+'</span><span>'+n+' 条下级</span><span>'+esc(x.deadline||"无期限")+'</span></div></button>'}).join("")||'<div class="empty-tip">没有匹配的大假设。</div>'}"""
hyp_js_lines.append(render_majors)

open_major = """function openMajor(id){currentMajor=id;currentMedium=null;var x=byId[id];if(!x)return;document.getElementById('hypModal').classList.add('open');document.getElementById('hypBreadcrumb').innerHTML='<button onclick="closeHyp()">首页</button><b>'+esc(x.title)+'</b>';var rows=(x.children||[]).map(function(cid){return byId[cid]}).filter(Boolean);document.getElementById('majorPane').innerHTML='<div class="detail-hero"><span class="level-pill">大假设</span><h2>'+esc(x.title)+'</h2><p>'+esc(x.rationale)+'</p>'+progress(x,true)+'<div class="meta-grid"><span>方向<br><b>'+esc(x.direction||"未标")+'</b></span><span>到期<br><b>'+esc(x.deadline||"无")+'</b></span><span>状态<br><b>'+statusLabel(x.status)+'</b></span><span>中假设<br><b>'+rows.length+'</b></span></div>'+(x.falsification_criteria?'<div class="note no-note">整体证伪标准：'+esc(x.falsification_criteria)+'</div>':"")+'</div><div class="pane-title">中假设（点击进入）</div>'+rows.map(renderMediumCard).join("")||'<div class="empty-tip">暂无中假设。</div>';document.getElementById('branchPane').innerHTML='<div class="empty-tip">选择一个中假设，查看小假设和验证阈值。</div>'}"""
hyp_js_lines.append(open_major)

render_medium = """function renderMediumCard(x){var kids=(x.children||[]).map(function(cid){return byId[cid]}).filter(Boolean);return '<button class="medium-card '+(currentMedium===x.id?"selected":"")+'" onclick="openMedium(\\''+x.id+'\\')"><div class="card-top"><span class="level-pill medium">中假设</span><span>'+kids.length+' 小假设</span></div><h4>'+esc(x.title)+'</h4>'+progress(x)+'<div class="card-foot"><span>'+esc(x.deadline||"无期限")+'</span><span>权重 '+esc(x.weight??"—")+'</span></div></button>'}"""
hyp_js_lines.append(render_medium)

open_medium = """function openMedium(id){currentMedium=id;var m=byId[id],major=byId[currentMajor];if(!m||!major)return;var mediums=(major.children||[]).map(function(cid){return byId[cid]}).filter(Boolean);document.getElementById('hypBreadcrumb').innerHTML='<button onclick="openMajor(\\''+currentMajor+'\\')">'+esc(major.title)+'</button><b>'+esc(m.title)+'</b>';document.getElementById('majorPane').innerHTML='<button class="back-btn" onclick="openMajor(\\''+currentMajor+'\\')">返回大假设</button><div class="detail-hero"><span class="level-pill medium">中假设</span><h2>'+esc(m.title)+'</h2><p>'+esc(m.rationale)+'</p>'+progress(m,true)+'<div class="meta-grid"><span>方向<br><b>'+esc(m.direction||"未标")+'</b></span><span>到期<br><b>'+esc(m.deadline||"无")+'</b></span><span>权重<br><b>'+esc(m.weight??"—")+'</b></span><span>状态<br><b>'+statusLabel(m.status)+'</b></span></div>'+(m.indicators||[]).map(indicatorBlock).join("")+(m.falsification_criteria?'<div class="note no-note">证伪标准：'+esc(m.falsification_criteria)+'</div>':"")+'</div><div class="pane-title">同组中假设</div>'+mediums.map(renderMediumCard).join("");var smalls=(m.children||[]).map(function(cid){return byId[cid]}).filter(Boolean);if(!smalls.length&&(m.indicators||[]).length)smalls=m.indicators.map(function(ind){return{title:ind.name,rationale:"验证来源："+(ind.source||"未填"),confidence:m.confidence,status:m.status,deadline:m.deadline,direction:m.direction,weight:m.weight,indicators:[ind]}});document.getElementById('branchPane').innerHTML='<div class="pane-title">小假设与验证指标</div>'+smalls.map(renderSmallCard).join("")||'<div class="empty-tip">暂无小假设。</div>'}"""
hyp_js_lines.append(open_medium)

render_small = """function renderSmallCard(x){return '<article class="small-card"><div class="card-top"><span class="level-pill small">小假设</span><span>'+statusLabel(x.status)+'</span></div><h4>'+esc(x.title)+'</h4><p>'+esc(x.rationale||"")+'</p>'+(x.indicators||[]).map(indicatorBlock).join("")+(x.falsification_criteria?'<div class="note no-note">证伪标准：'+esc(x.falsification_criteria)+'</div>':"")+'</article>'}"""
hyp_js_lines.append(render_small)

hyp_js_lines.append('function closeHyp(){document.getElementById("hypModal").classList.remove("open");currentMajor=null;currentMedium=null}')
hyp_js_lines.append('var currentMajor=null,currentMedium=null;')
hyp_js_lines.append('renderMajors();')
hyp_js_lines.append('</script>')

parts.append('\n'.join(hyp_js_lines))

# Intelligence JS
intel_js_lines = []
intel_js_lines.append('<script>')
intel_js_lines.append('var D=' + i_json + ';')
intel_js_lines.append("""var curSort="relevance";var curCat="";
var catNames={"macro":"宏观经济","finance":"金融市场","geopolitics":"地缘政治","energy":"能源安全","east_asia":"东亚","trade":"贸易","tech":"科技","social":"社会"};
function buildCatBtns(){var cats={};D.forEach(function(i){var c=i.category_cn||"other";cats[c]=(cats[c]||0)+1});var html='<button class="btn active" onclick="filterCat(\\'\\')">全部</button>';Object.keys(cats).sort(function(a,b){return cats[b]-cats[a]}).forEach(function(c){html+='<button class="btn" onclick="filterCat(\\''+c+'\\')">'+(catNames[c]||c)+' ('+cats[c]+')</button>'});document.getElementById("catBtns").innerHTML=html;document.getElementById("intelCount").textContent="("+D.length+" 条)"}
function filterCat(cat){curCat=cat;R(cat)}
function R(cat){var list=cat?D.filter(function(i){return i.category_cn===cat}):D;var sorted=list.slice();if(curSort==="time"){sorted.sort(function(a,b){return new Date(b.published_at||0)-new Date(a.published_at||0)})}else{sorted.sort(function(a,b){return (b.relevance||0)-(a.relevance||0)})}document.getElementById("il").innerHTML=sorted.map(function(i){var cc=i.category_cn||"other";var rv=i.relevance||0;var lang=(i.language||"en").toUpperCase();var pub=i.published_cn||"";var ago=i.time_ago||"";var kf=(i.key_facts&&i.key_facts.length)?i.key_facts.join("; "):"";var hasOrig=i.content_full&&i.content_full.length>30;return '<div class="item"><div class="meta-row"><span class="cat cat-'+cc+'">'+cc+'</span><span class="badge">'+lang+'</span><span class="rel rel-'+rv+'">R'+rv+'</span><span class="src">'+(i.source_name||"")+'</span></div><div class="meta-row"><span class="time-tag">'+pub+'</span><span class="time-tag">'+ago+'</span></div><div class="title">'+esc(i.cn_title||i.title||"")+'</div><div class="body">'+esc(i.cn_summary||"")+'</div>'+(kf?'<div class="kf">关键事实: '+esc(kf)+'</div>':'')+(i.impact?'<div class="impact">'+esc(i.impact)+'</div>':'')+(hasOrig?'<button class="expand-btn" onclick="tog(this)">展开原文</button><div class="orig">'+esc(i.content_full)+'</div>':'')+'</div>'}).join("")}
function S(s){curSort=s;document.getElementById("sort-relevance").classList.toggle("active",s==="relevance");document.getElementById("sort-time").classList.toggle("active",s==="time");R(curCat)}
function tog(btn){var c=btn.nextElementSibling;var open=c.style.display!=="block";c.style.display=open?"block":"none";btn.textContent=open?"收起原文":"展开原文"}
function esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
buildCatBtns();R("");""")
intel_js_lines.append('</script>')
intel_js_lines.append('<script>var _lastUpdate="' + now + '";</script>')

parts.append('\n'.join(intel_js_lines))
parts.append('\n</body>\n</html>')

html = ''.join(parts)

HTML_FILE.write_text(html, encoding='utf-8')

print(f"Dashboard v3 generated: {len(html)} bytes")
print(f"Intelligence: {len(intel)}")
print(f"Hypotheses: {len(hyps)} (majors: {len(major_ids)})")
