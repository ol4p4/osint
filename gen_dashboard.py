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

# ACH 矩阵数据（周循环产出，不存在时面板不显示）
ACH_FILE = Path(r"D:\osint\data\hypotheses\ach_matrix.json")
ach_data = None
try:
    with open(ACH_FILE, "r", encoding="utf-8") as f:
        ach_data = json.load(f)
except:
    pass

by_id = {h["id"]: h for h in hyps}
major_ids = [h["id"] for h in hyps if h.get("level") == "major"]
major_ids.sort(key=lambda x: by_id.get(x, {}).get("confidence", 0), reverse=True)

h_json = json.dumps(hyps, ensure_ascii=False)
m_json = json.dumps(major_ids, ensure_ascii=False)
i_json = json.dumps(intel, ensure_ascii=False)
ach_json = json.dumps(ach_data, ensure_ascii=False) if ach_data else "null"

# 宏观指标数据（fetch_macro_indicators.py 产出）
MACRO_FILE = Path(r"D:\osint\data\macro_indicators.json")
macro_data = None
try:
    with open(MACRO_FILE, "r", encoding="utf-8") as f:
        macro_data = json.load(f)
except:
    pass
macro_json = json.dumps(macro_data, ensure_ascii=False) if macro_data else "null"

now = datetime.now(timezone(timedelta(hours=8))).isoformat()

# Build HTML as list of parts
parts = []
parts.append('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>参谋系统</title>
<link rel="preconnect" href="https://fonts.loli.net">
<link href="https://fonts.loli.net/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg-deep:#0a0e14;--bg-card:#111820;--bg-surface:#1a2230;
  --border:#1e2d3d;--border-hover:#2d4a6a;
  --accent:#22d3ee;--accent-warm:#f59e0b;
  --danger:#ef4444;--success:#22c55e;
  --text:#e2e8f0;--text-dim:#64748b;--text-muted:#475569;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg-deep);color:var(--text);-webkit-font-smoothing:antialiased}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 16px;max-width:1440px;margin:0 auto}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.panel{background:var(--bg-card);border:1px solid var(--border);border-radius:6px;padding:16px;max-height:92vh;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.panel::-webkit-scrollbar{width:5px}
.panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:9px}
h2{font-family:"JetBrains Mono",monospace;font-size:13px;font-weight:600;margin-bottom:12px;color:var(--accent);letter-spacing:.5px;text-transform:uppercase}
.btns{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
.btn{padding:4px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg-surface);color:var(--text-dim);cursor:pointer;font-size:11px;font-family:"JetBrains Mono",monospace;transition:all .15s;letter-spacing:.3px}
.btn:hover,.btn.active{background:rgba(34,211,238,.12);border-color:var(--accent);color:var(--accent)}
.sort-btns{display:flex;gap:5px;margin-bottom:12px}
.item{background:var(--bg-deep);border:1px solid var(--border);border-radius:4px;padding:10px 12px;margin-bottom:6px;transition:all .15s;border-left:2px solid transparent}
.item:hover{transform:translateX(3px);border-color:var(--border-hover);border-left-color:var(--accent)}
.meta-row{display:flex;gap:5px;margin-bottom:5px;flex-wrap:wrap;align-items:center}
.cat{font-size:9px;padding:2px 6px;border-radius:3px;font-weight:600;font-family:"JetBrains Mono",monospace;letter-spacing:.3px;text-transform:uppercase}
.cat-macro,.cat-macroeconomics{background:rgba(168,130,247,.12);color:#a882f7}
.cat-finance,.cat-金融市场{background:rgba(245,158,11,.12);color:#f59e0b}
.cat-geopolitics,.cat-地缘政治{background:rgba(239,68,68,.12);color:#ef4444}
.cat-energy,.cat-能源安全{background:rgba(34,197,94,.12);color:#22c55e}
.cat-east_asia,.cat-东亚,.cat-东亚政治经济{background:rgba(168,130,247,.12);color:#c4b5fd}
.cat-trade,.cat-贸易,.cat-贸易政策{background:rgba(34,211,238,.12);color:#22d3ee}
.cat-tech,.cat-科技,.cat-科技产业{background:rgba(34,211,238,.08);color:#67e8f9}
.cat-social,.cat-社会{background:rgba(251,191,36,.12);color:#fbbf24}
.cat-other{background:rgba(100,116,139,.15);color:#94a3b8}
.badge{font-size:9px;padding:1px 5px;border-radius:3px;background:var(--bg-surface);color:var(--text-dim);font-family:"JetBrains Mono",monospace}
.rel{font-size:9px;padding:1px 5px;border-radius:3px;margin-left:auto;font-family:"JetBrains Mono",monospace}
.rel-5{background:rgba(239,68,68,.15);color:#f87171}
.rel-4{background:rgba(245,158,11,.15);color:#fbbf24}
.rel-3{background:rgba(34,197,94,.15);color:#4ade80}
.src{font-size:9px;color:var(--text-dim)}
.time-tag{font-size:9px;color:var(--text-muted);background:var(--bg-surface);padding:1px 6px;border-radius:3px;font-family:"JetBrains Mono",monospace}
.title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:3px;line-height:1.4}
.body{font-size:11px;color:var(--text-dim);line-height:1.55}
.impact{font-size:10px;color:var(--success);margin-top:5px;padding:4px 8px;background:rgba(34,197,94,.08);border-radius:3px;border-left:2px solid var(--success)}
#chatFab{position:fixed;right:26px;bottom:26px;width:52px;height:52px;border-radius:50%;background:var(--accent);color:var(--bg-deep);font-size:22px;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 0 20px rgba(34,211,238,.3);z-index:999;user-select:none;transition:all .2s}
#chatFab:hover{transform:scale(1.08);box-shadow:0 0 28px rgba(34,211,238,.45)}
#chatPanel{position:fixed;top:0;right:-460px;width:440px;height:100vh;background:var(--bg-deep);border-left:1px solid var(--border);z-index:1000;display:flex;flex-direction:column;transition:right .25s ease;box-shadow:-8px 0 24px rgba(0,0,0,.6)}
#chatPanel.open{right:0}
.chat-head{padding:12px 16px;background:var(--bg-card);border-bottom:1px solid var(--border);color:var(--text);font-family:"JetBrains Mono",monospace;font-size:12px;font-weight:600;display:flex;justify-content:space-between;align-items:center;letter-spacing:.5px}
.chat-head button{background:none;border:none;color:var(--text-dim);font-size:16px;cursor:pointer}
.chat-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.chat-msg{max-width:88%;padding:9px 12px;border-radius:4px;font-size:12px;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.chat-msg.user{align-self:flex-end;background:rgba(34,211,238,.15);color:var(--accent);border:1px solid rgba(34,211,238,.2);border-bottom-right-radius:2px}
.chat-msg.ai{align-self:flex-start;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-bottom-left-radius:2px}
.chat-msg.typing{color:var(--text-dim);font-style:italic}
.chat-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid var(--border);background:var(--bg-card)}
.chat-input-row textarea{flex:1;background:var(--bg-deep);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:8px;font-size:12px;resize:none;font-family:inherit}
.chat-input-row button{background:var(--accent);color:var(--bg-deep);border:none;border-radius:4px;padding:0 16px;cursor:pointer;font-size:12px;font-weight:600}
.chat-input-row button:hover{opacity:.9}
.impact.grad{color:var(--accent);background:rgba(34,211,238,.08);border-left-color:var(--accent)}
.dims{margin-top:6px;font-size:10px;color:var(--text-dim)}
.dims summary{cursor:pointer;color:var(--accent)}
.dims-body{margin-top:4px;padding:6px 8px;background:var(--bg-surface);border-radius:3px;line-height:1.6}
.dims-body b{color:var(--text)}
.kf{font-size:10px;color:var(--accent);margin-top:4px;padding:4px 8px;background:rgba(34,211,238,.06);border-radius:3px;border-left:2px solid var(--accent)}
.expand-btn{font-size:9px;padding:2px 8px;border:1px solid var(--border);border-radius:3px;background:var(--bg-surface);color:var(--text-dim);cursor:pointer;margin-top:6px;font-family:"JetBrains Mono",monospace}
.expand-btn:hover{background:var(--border);color:var(--text)}
.orig{display:none;font-size:10px;color:var(--text-muted);margin-top:8px;padding:8px;background:var(--bg-deep);border:1px solid var(--border);border-radius:3px;white-space:pre-wrap;max-height:200px;overflow-y:auto;font-family:"JetBrains Mono",monospace}
.strategy-shell{max-width:1440px;margin:0 auto;padding:20px 24px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;position:relative;overflow:hidden}
.strategy-shell::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),rgba(34,211,238,.1),transparent)}
.strategy-head{display:flex;gap:16px;align-items:center;justify-content:space-between;margin-bottom:16px}
.strategy-kicker{color:var(--accent);font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase}
.strategy-shell h1{font-family:"JetBrains Mono",monospace;font-size:22px;font-weight:700;color:var(--text);margin:5px 0;letter-spacing:-.5px}
.strategy-shell p{font-size:11px;color:var(--text-dim)}
#hypSearch{width:min(420px,42vw);padding:8px 12px;border:1px solid var(--border);border-radius:4px;background:var(--bg-deep);color:var(--text);font-family:"JetBrains Mono",monospace;font-size:11px}
#hypSearch::placeholder{color:var(--text-muted)}
#hypSearch:focus{outline:none;border-color:var(--accent)}
.major-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.major-card,.medium-card,.small-card{background:var(--bg-deep);border:1px solid var(--border);border-radius:4px;padding:12px 14px;text-align:left;color:var(--text);border-left:3px solid var(--border)}
.major-card{cursor:pointer;border-left-color:var(--accent);transition:.15s}
.major-card:hover{transform:translateY(-1px);border-color:var(--border-hover);border-left-color:var(--accent);box-shadow:0 4px 16px rgba(0,0,0,.3)}
.major-card h3{min-height:40px;font-size:14px;font-weight:600;line-height:1.4;margin:8px 0;color:var(--text)}
.major-card p,.medium-card p,.small-card p{min-height:30px;font-size:10px;color:var(--text-dim);line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-top{display:flex;justify-content:space-between;align-items:center;font-size:9px;color:var(--text-dim)}
.level-pill{padding:2px 7px;border-radius:3px;font-weight:700;font-family:"JetBrains Mono",monospace;font-size:9px;letter-spacing:.3px}
.level-pill{background:rgba(239,68,68,.12);color:#f87171}
.level-pill.medium{background:rgba(245,158,11,.12);color:#f59e0b}
.level-pill.small{background:rgba(34,211,238,.12);color:var(--accent)}
.card-foot{display:flex;justify-content:space-between;gap:6px;margin-top:8px;font-size:9px;color:var(--text-dim)}
.progress{position:relative;height:5px;margin-top:8px;background:var(--bg-surface);border-radius:99px;overflow:hidden}
.progress span{display:block;height:100%;border-radius:99px}
.progress b{position:absolute;right:6px;top:-2px;font-size:9px;font-family:"JetBrains Mono",monospace;color:var(--text);text-shadow:0 1px 3px rgba(0,0,0,.8)}
.progress-large{height:10px}
.progress-large b{top:-1px;font-size:10px}
.hyp-modal{position:fixed;inset:0;z-index:999;display:none;padding:24px;background:rgba(0,0,0,.7);backdrop-filter:blur(4px)}
.hyp-modal.open{display:flex;align-items:center;justify-content:center}
.hyp-modal-box{width:min(1300px,97vw);height:min(900px,94vh);overflow:hidden;background:var(--bg-deep);border:1px solid var(--border);border-radius:6px}
.hyp-modal-top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg-card)}
.breadcrumb{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-dim);overflow:hidden;white-space:nowrap}
.breadcrumb button{padding:4px 9px;border:1px solid var(--border);border-radius:3px;background:var(--bg-surface);color:var(--text);cursor:pointer;font-size:11px}
.breadcrumb b{color:var(--text);font-family:"JetBrains Mono",monospace;font-size:11px}
.modal-close{padding:6px 12px;border:0;border-radius:3px;background:var(--danger);color:white;cursor:pointer;font-size:11px;font-weight:600}
.hyp-modal-columns{display:grid;grid-template-columns:minmax(300px,380px) 1fr;height:calc(100% - 49px)}
.modal-pane{padding:16px;overflow:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.branch-pane{border-left:1px solid var(--border);background:var(--bg-card)}
.pane-title{margin:16px 0 9px;color:var(--accent);font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase}
.modal-pane>.pane-title:first-child{margin-top:0}
.medium-card{width:100%;margin-bottom:8px;cursor:pointer;border-left:3px solid var(--accent-warm)}
.medium-card:hover{border-color:var(--accent-warm)}
.medium-card.selected{border-color:var(--accent);background:var(--bg-card)}
.medium-card h4,.small-card h4{font-size:12px;line-height:1.4;margin:7px 0;color:var(--text);font-weight:600}
.back-btn{width:100%;margin-bottom:12px;padding:7px;border:1px solid var(--border);border-radius:3px;background:var(--bg-surface);color:var(--text);cursor:pointer;font-size:11px}
.detail-hero{padding:14px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);border-left:3px solid var(--accent)}
.detail-hero h2{font-size:17px;line-height:1.35;margin:8px 0;font-weight:600}
.detail-hero p{font-size:11px;line-height:1.55;color:var(--text-dim)}
.meta-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px;font-size:9px;color:var(--text-dim);text-align:center;font-family:"JetBrains Mono",monospace}
.meta-grid b{display:block;margin-top:3px;color:var(--text);font-size:10px}
.small-card{margin-bottom:8px;border-left:3px solid var(--accent)}
.indicator{display:grid;gap:3px;margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:3px;background:var(--bg-card);font-size:9px;color:var(--text-dim)}
.indicator b{color:var(--accent);font-size:10px;font-family:"JetBrains Mono",monospace}
.indicator .yes{color:var(--success)}
.indicator .no{color:var(--danger)}
.note{margin-top:8px;padding:7px;border-radius:3px;font-size:9px;line-height:1.45}
.no-note{background:rgba(239,68,68,.08);color:#fca5a5;border-left:2px solid var(--danger)}
.empty-tip{padding:16px;border:1px dashed var(--border);border-radius:4px;color:var(--text-muted);text-align:center;font-size:11px}
@media(max-width:850px){.strategy-head{align-items:stretch;flex-direction:column}#hypSearch{width:100%}.hyp-modal{padding:8px}.hyp-modal-box{height:96vh}.hyp-modal-columns{grid-template-columns:1fr;height:calc(100% - 49px)}.branch-pane{border-left:0;border-top:1px solid var(--border)}.meta-grid{grid-template-columns:repeat(2,1fr)}}
.ach-panel{margin-top:16px;padding:14px 16px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);position:relative}
.ach-panel::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#22c55e,rgba(34,197,94,.1),transparent)}
.ach-panel h2{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--success);margin-bottom:8px;letter-spacing:.5px}
.ach-panel .subtitle{font-size:10px;color:var(--text-dim);margin-bottom:10px}
.macro-panel{margin-top:16px;padding:14px 16px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);position:relative}
.macro-panel::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent-warm),rgba(245,158,11,.1),transparent)}
.macro-panel h2{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--accent-warm);margin-bottom:5px;letter-spacing:.5px}
.macro-panel .subtitle{font-size:9px;color:var(--text-dim);margin-bottom:10px}
.macro-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px}
.macro-cell{padding:8px 10px;border:1px solid var(--border);border-radius:3px;background:var(--bg-deep);font-size:10px}
.macro-cell .cat{font-size:8px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px;font-family:"JetBrains Mono",monospace}
.macro-cell .lab{color:var(--text-dim);font-size:9px;line-height:1.3;margin-bottom:4px;min-height:22px}
.macro-cell .val{font-size:15px;font-weight:600;color:var(--accent);font-family:"JetBrains Mono",monospace}
.macro-cell .meta{font-size:8px;color:var(--text-muted);margin-top:3px;font-family:"JetBrains Mono",monospace}
.macro-cell.stale{opacity:.4}
.macro-cell.stale .val{color:var(--danger)}
.ach-rank{display:flex;align-items:center;gap:10px;padding:7px 10px;margin-bottom:5px;background:var(--bg-deep);border:1px solid var(--border);border-radius:3px;cursor:pointer;transition:.15s;border-left:2px solid transparent}
.ach-rank:hover{border-color:var(--border-hover);border-left-color:var(--accent);background:var(--bg-card)}
.ach-rank .rank-num{font-family:"JetBrains Mono",monospace;font-size:16px;font-weight:800;color:var(--text-muted);min-width:26px;text-align:center}
.ach-rank .rank-num.top{color:var(--accent-warm)}
.ach-rank .rank-body{flex:1;min-width:0}
.ach-rank .rank-title{font-size:11px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}
.ach-rank .rank-meta{font-size:9px;color:var(--text-dim);margin-top:2px;font-family:"JetBrains Mono",monospace}
.ach-rank .rank-score{font-family:"JetBrains Mono",monospace;font-size:13px;font-weight:700;min-width:46px;text-align:right}
.ach-rank .rank-score.high{color:var(--success)}
.ach-rank .rank-score.mid{color:var(--accent-warm)}
.ach-rank .rank-score.low{color:var(--danger)}
.ach-bar{height:3px;background:var(--bg-surface);border-radius:99px;margin-top:4px;overflow:hidden}
.ach-bar span{display:block;height:100%;border-radius:99px}
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
  <div id="macroPanel" class="macro-panel" style="display:none">
    <h2>📊 宏观关键指标</h2>
    <div class="subtitle">汇率/利率/通胀/就业/增长 · 每日 FRED + Frankfurter + World Bank 抓取</div>
    <div id="macroGrid" class="macro-grid"></div>
    <div id="macroMeta" style="font-size:10px;color:#8b949e;margin-top:10px"></div>
  </div>
  <div id="achPanel" class="ach-panel" style="display:none">
    <h2>🔬 ACH 竞争性假设排名</h2>
    <div class="subtitle">CIA Heuer 方法论 · 贝叶斯后验置信度 · 证据越多越准</div>
    <div id="achRanks"></div>
  </div>
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
    <button class="btn" id="sort-relevance" onclick="S('relevance')">按相关度</button>
    <button class="btn active" id="sort-time" onclick="S('time')">按时间</button>
  </div>
  <div class="btns" id="catBtns"></div>
  <div id="il"></div>
</div>
</div>
''')

# Write hypothesis JS as plain string concatenation (no f-string issues)
hyp_js_lines = []
hyp_js_lines.append('<script>')
hyp_js_lines.append('function esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}')
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

# ACH 排名面板渲染
ach_js = """
var ACH_DATA=""" + ach_json + """;
(function(){
  if(!ACH_DATA||!ACH_DATA.scoring)return;
  var sc=ACH_DATA.scoring;
  var hyps=ACH_DATA.hypotheses||[];
  var rank=Object.keys(sc).map(function(id){
    var s=sc[id],h=hyps.find(function(x){return x.id===id})||{};
    return {id:id,title:h.title||id,posterior:s.posterior,support:s.support,refute:s.refute,prior:h.prior||0.5}
  }).sort(function(a,b){return b.posterior-a.posterior});
  if(!rank.length)return;
  document.getElementById('achPanel').style.display='';
  var html=rank.map(function(r,i){
    var cls=i<3?'top':'';
    var scoreCls=r.posterior>=0.6?'high':r.posterior>=0.35?'mid':'low';
    var barCol=r.posterior>=0.6?'#3fb950':r.posterior>=0.35?'#f0883e':'#f85149';
    return '<div class="ach-rank" onclick="openMajor(\\''+r.id+'\\')">'
      +'<span class="rank-num '+(i<3?cls:'')+'">'+(i+1)+'</span>'
      +'<div class="rank-body">'
      +'<div class="rank-title">'+esc(r.title)+'</div>'
      +'<div class="rank-meta">支持 '+r.support+' · 反驳 '+r.refute+' · 先验 '+Math.round(r.prior*100)+'%</div>'
      +'<div class="ach-bar"><span style="width:'+Math.round(r.posterior*100)+'%;background:'+barCol+'"></span></div>'
      +'</div>'
      +'<div class="rank-score '+scoreCls+'">'+Math.round(r.posterior*100)+'%</div>'
      +'</div>'
  }).join('');
  document.getElementById('achRanks').innerHTML=html;
  document.querySelector('.strategy-kicker').textContent+=' · ACH 排名';
})();
"""

# 宏观指标面板渲染
macro_js = """
var MACRO_DATA=""" + macro_json + """;
(function(){
  if(!MACRO_DATA||!MACRO_DATA.indicators)return;
  var inds=MACRO_DATA.indicators;
  var cells=Object.keys(inds).map(function(id){
    var i=inds[id];
    var cls=i.stale?'stale':'';
    var valStr;
    if(i.value===null||i.value===undefined){
      valStr='—';
    } else {
      try { valStr=i.fmt.replace('{','').replace('}','').split(':')[0]?i.fmt.replace(/\\{.*?\\}/, String(i.value)):(''+i.value); }
      catch(e){ valStr=String(i.value); }
    }
    // Use Number formatting via toFixed based on fmt precision
    var precision=2;
    var m=i.fmt&&i.fmt.match(/\\{(\\d+)\\.(\\d+)f\\}/);
    if(m) precision=parseInt(m[2]);
    valStr=typeof i.value==='number'?i.value.toFixed(precision):(i.value||'—');
    var dateStr=i.date?'数据 '+i.date:'';
    var srcStr=i.source?(i.source+(i.stale?' (stale '+i.stale_since+')':'')):'';
    return '<div class="macro-cell '+cls+'">'
      +'<div class="cat">'+esc(i.category)+'</div>'
      +'<div class="lab">'+esc(i.label)+'</div>'
      +'<div class="val">'+valStr+'</div>'
      +'<div class="meta">'+dateStr+'</div>'
      +'<div class="meta">'+srcStr+'</div>'
      +'</div>';
  }).join('');
  document.getElementById('macroGrid').innerHTML=cells;
  document.getElementById('macroPanel').style.display='';
  if(MACRO_DATA.updated_at){
    var dt=new Date(MACRO_DATA.updated_at);
    document.getElementById('macroMeta').textContent='最近抓取: '+dt.toLocaleString('zh-CN')+' · 共 '+Object.keys(inds).length+' 个指标';
  }
})();
"""
hyp_js_lines.append(ach_js)
hyp_js_lines.append(macro_js)

hyp_js_lines.append('</script>')

parts.append('\n'.join(hyp_js_lines))

# Intelligence JS
intel_js_lines = []
intel_js_lines.append('<script>')
intel_js_lines.append('var D=' + i_json + ';')
intel_js_lines.append("""var curSort="time";var curCat="";
var catNames={"macro":"宏观经济","finance":"金融市场","geopolitics":"地缘政治","energy":"能源安全","east_asia":"东亚","trade":"贸易","tech":"科技","social":"社会"};
function buildCatBtns(){var cats={};D.forEach(function(i){var c=i.category_cn||"other";cats[c]=(cats[c]||0)+1});var html='<button class="btn active" onclick="filterCat(\\'\\')">全部</button>';Object.keys(cats).sort(function(a,b){return cats[b]-cats[a]}).forEach(function(c){html+='<button class="btn" onclick="filterCat(\\''+c+'\\')">'+(catNames[c]||c)+' ('+cats[c]+')</button>'});document.getElementById("catBtns").innerHTML=html;document.getElementById("intelCount").textContent="("+D.length+" 条)"}
function filterCat(cat){curCat=cat;R(cat)}
function R(cat){var list=cat?D.filter(function(i){return i.category_cn===cat}):D;var sorted=list.slice();if(curSort==="time"){sorted.sort(function(a,b){return new Date(b.published_at||0)-new Date(a.published_at||0)})}else{sorted.sort(function(a,b){return (b.relevance||0)-(a.relevance||0)})}document.getElementById("il").innerHTML=sorted.map(function(i){var cc=i.category_cn||"other";var rv=i.relevance||0;var lang=(i.language||"en").toUpperCase();var pub=i.published_cn||"";if(!pub&&i.published_at){var pd=new Date(i.published_at);if(!isNaN(pd.getTime()))pub=pd.toLocaleDateString("zh-CN")}var ago=(function(){var pa=new Date(i.published_at||0);if(isNaN(pa.getTime()))return i.time_ago||"";var dm=(Date.now()-pa.getTime())/60000;if(dm<0)dm=0;return dm<1?"刚刚":dm<60?Math.round(dm)+"分钟前":dm<1440?Math.round(dm/60)+"小时前":Math.round(dm/1440)+"天前"})();var kf=(i.key_facts&&i.key_facts.length)?i.key_facts.join("; "):"";var hasOrig=i.content_full&&i.content_full.length>30;return '<div class="item"><div class="meta-row"><span class="cat cat-'+cc+'">'+cc+'</span><span class="badge">'+lang+'</span><span class="rel rel-'+rv+'">R'+rv+'</span><span class="src">'+(i.source_name||"")+'</span></div><div class="meta-row"><span class="time-tag">'+pub+'</span><span class="time-tag">'+ago+'</span></div><div class="title">'+esc(i.cn_title||i.title||"")+'</div><div class="body">'+esc(i.cn_summary||"")+'</div>'+(kf?'<div class="kf">关键事实: '+esc(kf)+'</div>':'')+(i.impact?'<div class="impact">👤 '+esc(i.impact)+'</div>':'')+(i.graduate_impact?'<div class="impact grad">🎓 '+esc(i.graduate_impact)+'</div>':'')+(i.dims?'<details class="dims"><summary>四维诊断</summary><div class="dims-body">'+Object.entries(i.dims).map(function(p){return '<div><b>'+({accumulation_node:'积累制度',spatial_layer:'空间修正',state_market_shift:'国家-市场',class_interest:'阶级利益'}[p[0]]||p[0])+'</b>：'+esc(p[1])+'</div>'}).join('')+'</div></details>':'')+(hasOrig?'<button class="expand-btn" onclick="tog(this)">展开原文</button><div class="orig">'+esc(i.content_full)+'</div>':'')+'</div>'}).join("")}
function S(s){curSort=s;document.getElementById("sort-relevance").classList.toggle("active",s==="relevance");document.getElementById("sort-time").classList.toggle("active",s==="time");R(curCat)}
function toggleChat(){var p=document.getElementById("chatPanel");p.classList.toggle("open");if(p.classList.contains("open"))document.getElementById("chatInput").focus()}
function pushMsg(cls,text){var box=document.getElementById("chatMsgs");var div=document.createElement("div");div.className="chat-msg "+cls;div.textContent=text;box.appendChild(div);box.scrollTop=box.scrollHeight}
async function sendChat(){var inp=document.getElementById("chatInput");var q=inp.value.trim();if(!q)return;inp.value="";pushMsg("user",q);var box=document.getElementById("chatMsgs");var tip=document.createElement("div");tip.className="chat-msg ai typing";tip.textContent="参谋长研判中……";box.appendChild(tip);box.scrollTop=box.scrollHeight;try{var r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({q:q})});var d=await r.json();tip.remove();pushMsg("ai",d.answer||("出错: "+(d.error||"未知错误")))}catch(e){tip.remove();pushMsg("ai","请求失败: "+e)}}
document.addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey&&document.activeElement===document.getElementById("chatInput")){e.preventDefault();sendChat()}});
function tog(btn){var c=btn.nextElementSibling;var open=c.style.display!=="block";c.style.display=open?"block":"none";btn.textContent=open?"收起原文":"展开原文"}
function esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
buildCatBtns();R("");""")
intel_js_lines.append('</script>')
intel_js_lines.append('<script>var _lastUpdate="' + now + '";</script>')

parts.append('\n'.join(intel_js_lines))
parts.append('''
<div id="chatFab" onclick="toggleChat()" title="向参谋长提问 / 提出假设">💬</div>
<div id="chatPanel">
  <div class="chat-head">🎓 参谋长 · 四维研判对话 <button onclick="toggleChat()">✕</button></div>
  <div id="chatMsgs" class="chat-msgs"><div class="chat-msg ai">我是你的参谋长。可以向我提问（"如果美联储9月降息对我意味着什么？"）或提出你的假设，我会按四维框架给你研判、行动向量与避坑提示。</div></div>
  <div class="chat-input-row">
    <textarea id="chatInput" placeholder="输入问题或假设，Enter 发送（Shift+Enter 换行）" rows="2"></textarea>
    <button onclick="sendChat()">发送</button>
  </div>
</div>
''')
parts.append('\n</body>\n</html>')

html = ''.join(parts)

HTML_FILE.write_text(html, encoding='utf-8')

print(f"Dashboard v3 generated: {len(html)} bytes")
print(f"Intelligence: {len(intel)}")
print(f"Hypotheses: {len(hyps)} (majors: {len(major_ids)})")
