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
generated_at = data.get("generated_at", datetime.now(timezone(timedelta(hours=8))).isoformat())

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
mega_ids = [h["id"] for h in hyps if h.get("level") == "mega"]
mega_json = json.dumps(mega_ids, ensure_ascii=False)
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

# 失业率历史时间序列
UNRATE_FILE = Path(r"D:\osint\data\cn_unemployment_history.json")
unrate_data = None
try:
    with open(UNRATE_FILE, "r", encoding="utf-8") as f:
        unrate_data = json.load(f)
except:
    pass
unrate_json = json.dumps(unrate_data, ensure_ascii=False) if unrate_data else "null"

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
  --bg:#ffffff;--bg-subtle:#f8f9fa;--bg-muted:#f1f3f5;
  --border:#e5e7eb;--border-hover:#d1d5db;
  --accent:#2563eb;--accent-light:#eff6ff;
  --text:#111827;--text-secondary:#6b7280;--text-muted:#9ca3af;
  --success:#16a34a;--danger:#dc2626;--warning:#d97706;
  --shadow-sm:0 1px 2px rgba(0,0,0,.05);
  --shadow:0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06);
  --shadow-md:0 4px 6px rgba(0,0,0,.07),0 2px 4px rgba(0,0,0,.06);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg-subtle);color:var(--text);-webkit-font-smoothing:antialiased;line-height:1.5}

/* ===== Layout ===== */
.container{max-width:1400px;margin:0 auto;padding:20px 24px 80px}
.section{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:var(--shadow-sm)}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;gap:12px;flex-wrap:wrap}
.section-title{font-family:"JetBrains Mono",monospace;font-size:13px;font-weight:600;color:var(--text-secondary);letter-spacing:.5px;text-transform:uppercase}
.section-subtitle{font-size:11px;color:var(--text-muted);margin-top:2px}

/* ===== Header ===== */
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 0;margin-bottom:4px}
.header h1{font-size:20px;font-weight:700;color:var(--text);letter-spacing:-.3px}
.header h1 span{color:var(--accent)}
.header-meta{font-size:11px;color:var(--text-muted);font-family:"JetBrains Mono",monospace}
.header-updated{font-size:11px;color:var(--text-secondary);font-family:"JetBrains Mono",monospace;background:var(--bg-subtle);padding:6px 12px;border-radius:6px;border:1px solid var(--border);cursor:help}

/* ===== KPI Bar ===== */
.kpi-bar{display:grid;grid-template-columns:1fr 340px;gap:16px}
@media(max-width:900px){.kpi-bar{grid-template-columns:1fr}}
.kpi-macro{min-width:0}
.kpi-ach{border-left:1px solid var(--border);padding-left:16px}
@media(max-width:900px){.kpi-ach{border-left:0;border-top:1px solid var(--border);padding-left:0;padding-top:16px}}

/* Macro grid inside KPI */
.macro-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
@media(max-width:1100px){.macro-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:600px){.macro-grid{grid-template-columns:repeat(2,1fr)}}
.macro-cell{padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);font-size:11px;transition:box-shadow .15s}
.macro-cell:hover{box-shadow:var(--shadow)}
.macro-cell .cat{font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;font-family:"JetBrains Mono",monospace}
.macro-cell .lab{color:var(--text-secondary);font-size:10px;line-height:1.3;margin-bottom:5px;min-height:24px}
.macro-cell .val{font-size:16px;font-weight:600;color:var(--text);font-family:"JetBrains Mono",monospace}
.macro-cell .meta{font-size:9px;color:var(--text-muted);margin-top:4px;font-family:"JetBrains Mono",monospace}
.macro-cell.stale{opacity:.45}
.macro-cell.stale .val{color:var(--danger)}

/* Mega hypotheses */
.mega-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
.mega-card{padding:16px;border:1px solid var(--border);border-radius:8px;background:linear-gradient(135deg,var(--bg) 0%,var(--bg-subtle) 100%);transition:box-shadow .15s,border-color .15s;border-left:4px solid #7c3aed}
.mega-card:hover{box-shadow:var(--shadow);border-color:#7c3aed}
.mega-card .mega-level{font-size:9px;color:#7c3aed;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-family:"JetBrains Mono",monospace;font-weight:700}
.mega-card .mega-title{font-size:15px;font-weight:700;color:var(--text);margin-bottom:6px;line-height:1.3}
.mega-card .mega-rationale{font-size:12px;color:var(--text-secondary);line-height:1.5;margin-bottom:10px}
.mega-card .mega-indicators{margin-top:10px;padding-top:10px;border-top:1px dashed var(--border);font-size:10px}
.mega-card .mega-indicators b{color:var(--text);font-weight:600;display:block;margin-bottom:4px;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.mega-card .mega-ind{color:var(--text-muted);line-height:1.5;padding:2px 0}
.mega-card .mega-ind b{color:var(--text-secondary);font-weight:500;font-size:10px;display:inline;margin:0;text-transform:none;letter-spacing:0}
.mega-card .mega-conf{font-size:24px;font-weight:700;color:#7c3aed;font-family:"JetBrains Mono",monospace;float:right;line-height:1}

/* Trend section */
.trend-legend{display:flex;flex-wrap:wrap;gap:12px 18px;margin-top:12px;font-size:11px;color:var(--text-secondary)}
.legend-item{display:flex;align-items:center;gap:6px}
.legend-item i{width:10px;height:10px;display:inline-block;border-radius:2px}
.trend-note{font-size:10px;color:var(--text-muted);margin-top:8px;line-height:1.4}
#unrateChart{width:100%!important;max-height:280px}

/* ACH ranking inside KPI */
.ach-title{font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:10px;font-family:"JetBrains Mono",monospace;letter-spacing:.3px}
.ach-rank{display:flex;align-items:center;gap:10px;padding:8px 10px;margin-bottom:5px;border:1px solid var(--border);border-radius:6px;cursor:pointer;transition:all .15s;background:var(--bg)}
.ach-rank:hover{border-color:var(--accent);box-shadow:var(--shadow)}
.ach-rank .rank-num{font-family:"JetBrains Mono",monospace;font-size:15px;font-weight:800;color:var(--text-muted);min-width:24px;text-align:center}
.ach-rank .rank-num.top{color:var(--accent)}
.ach-rank .rank-body{flex:1;min-width:0}
.ach-rank .rank-title{font-size:11px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}
.ach-rank .rank-meta{font-size:9px;color:var(--text-muted);margin-top:2px;font-family:"JetBrains Mono",monospace}
.ach-rank .rank-score{font-family:"JetBrains Mono",monospace;font-size:13px;font-weight:700;min-width:44px;text-align:right}
.ach-rank .rank-score.high{color:var(--success)}
.ach-rank .rank-score.mid{color:var(--warning)}
.ach-rank .rank-score.low{color:var(--danger)}
.ach-bar{height:3px;background:var(--bg-muted);border-radius:99px;margin-top:4px;overflow:hidden}
.ach-bar span{display:block;height:100%;border-radius:99px}

/* ===== Intel Feed ===== */
.section-collapsible{border:1px solid var(--border);border-radius:8px;background:var(--bg);margin-bottom:24px;overflow:hidden}
.section-collapsible>summary{list-style:none;cursor:pointer;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;user-select:none;background:var(--bg-subtle);border-bottom:1px solid transparent;transition:background .15s}
.section-collapsible>summary:hover{background:var(--bg-muted)}
.section-collapsible>summary::-webkit-details-marker{display:none}
.section-collapsible>summary::before{content:"▶";font-size:10px;margin-right:10px;color:var(--text-muted);transition:transform .15s;display:inline-block}
.section-collapsible[open]>summary::before{transform:rotate(90deg)}
.section-collapsible[open]>summary{border-bottom-color:var(--border)}
.section-toggle-hint{font-size:10px;color:var(--text-muted);font-family:"JetBrains Mono",monospace}
.section-body{padding:14px 18px 18px;max-height:600px;overflow-y:auto}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.btn{padding:5px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text-secondary);cursor:pointer;font-size:11px;font-family:"Inter",sans-serif;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.active{background:var(--accent);border-color:var(--accent);color:#fff}
.sort-btns{display:flex;gap:6px}
.item{border:1px solid var(--border);border-radius:6px;padding:12px 14px;margin-bottom:8px;transition:all .15s;background:var(--bg);border-left:3px solid transparent}
.item:hover{border-color:var(--border-hover);border-left-color:var(--accent);box-shadow:var(--shadow-sm)}
.meta-row{display:flex;gap:6px;margin-bottom:5px;flex-wrap:wrap;align-items:center}
.cat{font-size:9px;padding:2px 7px;border-radius:4px;font-weight:600;letter-spacing:.3px}
.cat-macro,.cat-macroeconomics{background:#f3e8ff;color:#7c3aed}
.cat-finance,.cat-金融市场{background:#fef3c7;color:#d97706}
.cat-geopolitics,.cat-地缘政治{background:#fee2e2;color:#dc2626}
.cat-energy,.cat-能源安全{background:#dcfce7;color:#16a34a}
.cat-east_asia,.cat-东亚,.cat-东亚政治经济{background:#ede9fe;color:#7c3aed}
.cat-trade,.cat-贸易,.cat-贸易政策{background:#dbeafe;color:#2563eb}
.cat-tech,.cat-科技,.cat-科技产业{background:#e0f2fe;color:#0284c7}
.cat-social,.cat-社会{background:#fff7ed;color:#ea580c}
.cat-other{background:#f3f4f6;color:#6b7280}
.badge{font-size:9px;padding:1px 6px;border-radius:4px;background:var(--bg-muted);color:var(--text-muted);font-family:"JetBrains Mono",monospace}
.rel{font-size:9px;padding:1px 6px;border-radius:4px;margin-left:auto;font-family:"JetBrains Mono",monospace;font-weight:600}
.rel-5{background:#fee2e2;color:#dc2626}
.rel-4{background:#fef3c7;color:#d97706}
.rel-3{background:#dcfce7;color:#16a34a}
.src{font-size:10px;color:var(--text-muted)}
.time-tag{font-size:9px;color:var(--text-muted);background:var(--bg-muted);padding:2px 7px;border-radius:4px;font-family:"JetBrains Mono",monospace}
.item-title{font-size:14px;font-weight:600;color:var(--text);margin-bottom:4px;line-height:1.45}
.item-body{font-size:12px;color:var(--text-secondary);line-height:1.6}
.impact{font-size:11px;color:var(--success);margin-top:6px;padding:5px 10px;background:#f0fdf4;border-radius:4px;border-left:2px solid var(--success)}
.impact.grad{color:var(--accent);background:var(--accent-light);border-left-color:var(--accent)}
.kf{font-size:11px;color:var(--accent);margin-top:5px;padding:5px 10px;background:var(--accent-light);border-radius:4px;border-left:2px solid var(--accent)}
.dims{margin-top:6px;font-size:11px;color:var(--text-secondary)}
.dims summary{cursor:pointer;color:var(--accent);font-weight:500}
.dims-body{margin-top:4px;padding:8px 10px;background:var(--bg-muted);border-radius:4px;line-height:1.7}
.dims-body b{color:var(--text)}
.expand-btn{font-size:10px;padding:3px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text-secondary);cursor:pointer;margin-top:6px;transition:all .15s}
.expand-btn:hover{background:var(--bg-muted);color:var(--text)}
.orig{display:none;font-size:11px;color:var(--text-muted);margin-top:8px;padding:10px;background:var(--bg-muted);border:1px solid var(--border);border-radius:4px;white-space:pre-wrap;max-height:200px;overflow-y:auto;font-family:"JetBrains Mono",monospace}

/* ===== Hypothesis Cockpit ===== */
#hypSearch{width:min(420px,42vw);padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px;transition:border-color .15s}
#hypSearch::placeholder{color:var(--text-muted)}
#hypSearch:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.major-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.major-card,.medium-card,.small-card{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px 16px;text-align:left;color:var(--text);border-left:3px solid var(--border);transition:all .15s}
.major-card{cursor:pointer;border-left-color:var(--accent)}
.major-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-md);border-color:var(--border-hover);border-left-color:var(--accent)}
.major-card h3{min-height:42px;font-size:15px;font-weight:600;line-height:1.4;margin:8px 0;color:var(--text)}
.major-card p,.medium-card p,.small-card p{min-height:32px;font-size:11px;color:var(--text-secondary);line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-top{display:flex;justify-content:space-between;align-items:center;font-size:9px;color:var(--text-muted)}
.level-pill{padding:2px 8px;border-radius:4px;font-weight:700;font-size:9px;letter-spacing:.3px}
.level-pill{background:#fee2e2;color:#dc2626}
.level-pill.medium{background:#fef3c7;color:#d97706}
.level-pill.small{background:var(--accent-light);color:var(--accent)}
.card-foot{display:flex;justify-content:space-between;gap:6px;margin-top:8px;font-size:9px;color:var(--text-muted)}
.progress{position:relative;height:5px;margin-top:8px;background:var(--bg-muted);border-radius:99px;overflow:hidden}
.progress span{display:block;height:100%;border-radius:99px}
.progress b{position:absolute;right:6px;top:-2px;font-size:9px;font-family:"JetBrains Mono",monospace;color:var(--text);text-shadow:0 0 3px #fff}
.progress-large{height:10px}
.progress-large b{top:-1px;font-size:10px}

/* ===== Modal ===== */
.hyp-modal{position:fixed;inset:0;z-index:999;display:none;padding:24px;background:rgba(0,0,0,.4);backdrop-filter:blur(4px)}
.hyp-modal.open{display:flex;align-items:center;justify-content:center}
.hyp-modal-box{width:min(1300px,97vw);height:min(900px,94vh);overflow:hidden;background:var(--bg);border:1px solid var(--border);border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.15)}
.hyp-modal-top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--bg-subtle)}
.breadcrumb{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-secondary);overflow:hidden;white-space:nowrap}
.breadcrumb button{padding:5px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px;transition:all .15s}
.breadcrumb button:hover{border-color:var(--accent);color:var(--accent)}
.breadcrumb b{color:var(--text);font-family:"JetBrains Mono",monospace;font-size:11px}
.modal-close{padding:6px 14px;border:0;border-radius:6px;background:var(--danger);color:white;cursor:pointer;font-size:11px;font-weight:600;transition:opacity .15s}
.modal-close:hover{opacity:.85}
.hyp-modal-columns{display:grid;grid-template-columns:minmax(300px,380px) 1fr;height:calc(100% - 53px)}
.modal-pane{padding:16px;overflow:auto}
.branch-pane{border-left:1px solid var(--border);background:var(--bg-subtle)}
.pane-title{margin:16px 0 9px;color:var(--accent);font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase}
.modal-pane>.pane-title:first-child{margin-top:0}
.medium-card{width:100%;margin-bottom:8px;cursor:pointer;border-left:3px solid var(--warning)}
.medium-card:hover{border-color:var(--warning)}
.medium-card.selected{border-color:var(--accent);background:var(--accent-light)}
.medium-card h4,.small-card h4{font-size:12px;line-height:1.4;margin:7px 0;color:var(--text);font-weight:600}
.back-btn{width:100%;margin-bottom:12px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px;transition:all .15s}
.back-btn:hover{border-color:var(--accent);color:var(--accent)}
.detail-hero{padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--bg);border-left:3px solid var(--accent)}
.detail-hero h2{font-size:17px;line-height:1.35;margin:8px 0;font-weight:600}
.detail-hero p{font-size:11px;line-height:1.55;color:var(--text-secondary)}
.meta-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px;font-size:9px;color:var(--text-secondary);text-align:center;font-family:"JetBrains Mono",monospace}
.meta-grid b{display:block;margin-top:3px;color:var(--text);font-size:10px}
.small-card{margin-bottom:8px;border-left:3px solid var(--accent)}
.indicator{display:grid;gap:3px;margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg-subtle);font-size:10px;color:var(--text-secondary)}
.indicator b{color:var(--accent);font-size:11px;font-family:"JetBrains Mono",monospace}
.indicator .yes{color:var(--success)}
.indicator .no{color:var(--danger)}
.note{margin-top:8px;padding:8px;border-radius:6px;font-size:10px;line-height:1.45}
.no-note{background:#fef2f2;color:#b91c1c;border-left:2px solid var(--danger)}
.empty-tip{padding:16px;border:1px dashed var(--border);border-radius:6px;color:var(--text-muted);text-align:center;font-size:11px}

/* ===== Chat ===== */
#chatFab{position:fixed;right:26px;bottom:26px;width:52px;height:52px;border-radius:50%;background:var(--accent);color:#fff;font-size:22px;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 12px rgba(37,99,235,.3);z-index:999;user-select:none;transition:all .2s}
#chatFab:hover{transform:scale(1.08);box-shadow:0 6px 20px rgba(37,99,235,.4)}
#chatPanel{position:fixed;top:0;right:-460px;width:440px;height:100vh;background:var(--bg);border-left:1px solid var(--border);z-index:1000;display:flex;flex-direction:column;transition:right .25s ease;box-shadow:-8px 0 24px rgba(0,0,0,.1)}
#chatPanel.open{right:0}
.chat-head{padding:12px 16px;background:var(--bg-subtle);border-bottom:1px solid var(--border);color:var(--text);font-size:13px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
.chat-head button{background:none;border:none;color:var(--text-muted);font-size:16px;cursor:pointer}
.chat-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.chat-msg{max-width:88%;padding:10px 14px;border-radius:8px;font-size:12.5px;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.chat-msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:2px}
.chat-msg.ai{align-self:flex-start;background:var(--bg-subtle);color:var(--text);border:1px solid var(--border);border-bottom-left-radius:2px}
.chat-msg.typing{color:var(--text-muted);font-style:italic}
.chat-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid var(--border);background:var(--bg-subtle)}
.chat-input-row textarea{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12.5px;resize:none;font-family:inherit}
.chat-input-row textarea:focus{outline:none;border-color:var(--accent)}
.chat-input-row button{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:0 16px;cursor:pointer;font-size:12px;font-weight:600;transition:opacity .15s}
.chat-input-row button:hover{opacity:.85}

/* ===== Responsive ===== */
@media(max-width:850px){.header{flex-direction:column;align-items:flex-start}#hypSearch{width:100%}.hyp-modal{padding:8px}.hyp-modal-box{height:96vh}.hyp-modal-columns{grid-template-columns:1fr;height:calc(100% - 53px)}.branch-pane{border-left:0;border-top:1px solid var(--border)}.meta-grid{grid-template-columns:repeat(2,1fr)}.kpi-bar{grid-template-columns:1fr}.kpi-ach{border-left:0;border-top:1px solid var(--border);padding-left:0;padding-top:16px}}
</style>
<link rel="icon" href="data:,">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div class="container">

<!-- Header -->
<div class="header">
  <div>
    <h1>参谋系统 · <span>战略假设驾驶舱</span></h1>
    <div class="header-meta">四维研判框架 · CIA Heuer ACH · 贝叶斯更新</div>
  </div>
  <div class="header-updated" id="headerUpdated">最近更新: --</div>
</div>

<!-- KPI Bar: Macro + ACH -->
<div class="section">
  <div class="kpi-bar">
    <div class="kpi-macro">
      <div class="section-title">📊 宏观关键指标</div>
      <div class="section-subtitle">汇率 / 利率 / 通胀 / 就业 / 增长 · 每日自动抓取</div>
      <div id="macroPanel" style="display:none;margin-top:12px">
        <div id="macroGrid" class="macro-grid"></div>
        <div id="macroMeta" style="font-size:10px;color:var(--text-muted);margin-top:8px"></div>
      </div>
    </div>
    <div class="kpi-ach">
      <div class="ach-title">🔬 ACH 竞争性假设排名</div>
      <div id="achPanel" style="display:none">
        <div id="achMeta" style="font-size:10px;color:var(--text-muted);margin-bottom:8px;font-family:'JetBrains Mono',monospace"></div>
        <div id="achRanks"></div>
      </div>
      <div id="achEmpty" class="empty-tip" style="margin-top:8px">等待周循环产出 ACH 矩阵…</div>
    </div>
  </div>
</div>

<!-- Trend Section: 中国分年龄组失业率走势 -->
<div class="section">
  <div class="section-header">
    <div>
      <div class="section-title">📈 中国分年龄组失业率走势</div>
      <div class="section-subtitle">16-24岁（不含在校生）核心指标 · 月度 NBS 数据 · 财新转载抓取</div>
    </div>
  </div>
  <div id="unratePanel" style="display:none">
    <canvas id="unrateChart" height="260"></canvas>
    <div class="trend-legend">
      <span class="legend-item"><i style="background:#dc2626"></i>16-24岁（不含在校生）</span>
      <span class="legend-item"><i style="background:#f59e0b"></i>25-29岁</span>
      <span class="legend-item"><i style="background:#3b82f6"></i>30-59岁</span>
      <span class="legend-item"><i style="background:#6b7280"></i>总失业率</span>
      <span class="legend-item"><i style="background:#9ca3af;border:1px dashed #9ca3af"></i>15-24岁 ILO 参考线 (年度)</span>
    </div>
    <p class="trend-note">注：NBS 2023-08~11 停发; 2023-12 起新口径"不含在校生"与历史不可比; 数据每月20日自动抓取</p>
  </div>
  <div id="unrateEmpty" class="empty-tip">等待历史数据加载…</div>
</div>

<!-- Intel Feed -->
<details class="section section-collapsible" id="intelSection">
  <summary>
    <div class="section-title">📡 情报流 <span id="intelCount" style="font-weight:400;color:var(--text-muted)"></span></div>
    <span class="section-toggle-hint">点击展开 ▾</span>
  </summary>
  <div class="section-body">
    <div class="filter-row">
      <div class="sort-btns">
        <button class="btn" id="sort-relevance" onclick="S('relevance')">按相关度</button>
        <button class="btn active" id="sort-time" onclick="S('time')">按时间</button>
      </div>
      <div class="btns" id="catBtns"></div>
    </div>
    <div id="il"></div>
  </div>
</details>

<!-- Mega Hypotheses (超大假设) -->
<div class="section">
  <div class="section-header">
    <div>
      <div class="section-title">🌐 超大假设 <span style="font-weight:400;color:var(--text-muted);font-size:12px">概率极低 × 影响最大 × 跨大假设关联</span></div>
      <div class="section-subtitle">战略级 mega 假设 · 历史先例驱动 · 不进 ACH 周循环 (单次 AI 成本太高)</div>
    </div>
    <div class="filter-row">
      <input id="megaSearch" placeholder="搜索三战 / 经济危机 / 金融危机…" oninput="renderMegas()" style="padding:5px 10px;border:1px solid var(--border);border-radius:6px;font-size:11px;width:220px">
    </div>
  </div>
  <div id="megaGrid" class="mega-grid"></div>
  <div id="megaEmpty" class="empty-tip">尚无 mega 假设</div>
</div>

<!-- Hypothesis Cockpit -->
<div class="section">
  <div class="section-header">
    <div>
      <div class="section-title">🎯 假设驾驶舱</div>
      <div class="section-subtitle">点击大假设 → 中假设 → 小假设与验证指标</div>
    </div>
    <input id="hypSearch" placeholder="搜索三战、经济危机、城投、AI、台海…" oninput="renderMajors()">
  </div>
  <div id="majorGrid" class="major-grid"></div>
</div>

</div><!-- /container -->

<!-- Hypothesis Modal -->
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
hyp_js_lines.append('function progress(x,big){var c=x.confidence||0;var col=c>=0.7?"#16a34a":c>=0.4?"#d97706":"#dc2626";var cls=big?"progress-large":"";return \'<div class="progress \'+cls+\'"><span style="width:\'+Math.round(c*100)+\'%;background:\'+col+\'"></span><b>\'+Math.round(c*100)+\'%</b></div>\'}')
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
  document.getElementById('achEmpty').style.display='none';
  var sc=ACH_DATA.scoring;
  var hyps=ACH_DATA.hypotheses||[];
  // 显示诊断时间
  var meta=document.getElementById('achMeta');
  if(meta){
    var upd=ACH_DATA.updated_at||ACH_DATA.updated;
    if(upd){
      var dt=new Date(upd);
      var local=isNaN(dt)?upd:dt.toLocaleString('zh-CN',{hour12:false});
      meta.textContent='诊断时间: '+local+' (UTC)';
    } else {
      meta.textContent='诊断时间: 未知';
    }
  }
  var rank=Object.keys(sc).map(function(id){
    var s=sc[id],h=hyps.find(function(x){return x.id===id})||{};
    return {id:id,title:h.title||id,posterior:s.posterior,support:s.support,refute:s.refute,prior:h.prior||0.5}
  }).sort(function(a,b){return b.posterior-a.posterior});
  if(!rank.length)return;
  document.getElementById('achPanel').style.display='';
  var html=rank.map(function(r,i){
    var cls=i<3?'top':'';
    var scoreCls=r.posterior>=0.6?'high':r.posterior>=0.35?'mid':'low';
    var barCol=r.posterior>=0.6?'#16a34a':r.posterior>=0.35?'#d97706':'#dc2626';
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
})();
"""

# 失业率历史时间序列渲染
unrate_js = """
var UNRATE_DATA=""" + unrate_json + """;
var UNRATE_CHART=null;
(function(){
  if(!UNRATE_DATA||!UNRATE_DATA.series)return;
  var empty=document.getElementById('unrateEmpty');
  var panel=document.getElementById('unratePanel');
  if(!panel)return;
  var s=UNRATE_DATA.series;
  var hasAny=Object.keys(s).some(function(k){return (s[k]||[]).length>0;});
  if(!hasAny){if(empty)empty.style.display='block';return;}
  if(empty)empty.style.display='none';
  panel.style.display='block';
  // 收集所有日期点
  var dateSet={};
  Object.keys(s).forEach(function(k){
    (s[k]||[]).forEach(function(p){dateSet[p.date]=true;});
  });
  var labels=Object.keys(dateSet).sort();
  function buildSeries(key,color,opts){
    var pts={};
    (s[key]||[]).forEach(function(p){pts[p.date]=p.value;});
    return {
      label: opts&&opts.label||key,
      data: labels.map(function(d){return pts[d]!==undefined?pts[d]:null;}),
      borderColor: color,
      backgroundColor: color+'20',
      borderWidth: opts&&opts.dashed?1.5:2,
      borderDash: opts&&opts.dashed?[5,5]:[],
      pointRadius: 2.5,
      tension: 0.25,
      spanGaps: false,
      fill: false,
    };
  }
  var datasets=[
    buildSeries('cn_youth_unrate_16_24','#dc2626',{label:'16-24岁 (不含在校生)'}),
    buildSeries('cn_unrate_25_29','#f59e0b',{label:'25-29岁'}),
    buildSeries('cn_unrate_30_59','#3b82f6',{label:'30-59岁'}),
    buildSeries('cn_unrate_total','#6b7280',{label:'总失业率'}),
    buildSeries('cn_youth_15_24_ilo_annual','#9ca3af',{label:'15-24岁 ILO参考 (年度)',dashed:true}),
  ].filter(function(d){return d.data.some(function(v){return v!==null;});});
  var ctx=document.getElementById('unrateChart');
  if(!ctx)return;
  if(typeof Chart==='undefined'){
    // CDN 失败,降级到简单文本列表
    var rows=labels.map(function(d){
      var line=d+': ';
      datasets.forEach(function(ds){
        var v=ds.data[labels.indexOf(d)];
        if(v!==null&&v!==undefined) line+=ds.label+'='+v+'% ';
      });
      return line;
    }).join('<br>');
    ctx.outerHTML='<div style="font-size:11px;font-family:monospace">'+rows+'</div>';
    return;
  }
  UNRATE_CHART=new Chart(ctx,{
    type:'line',
    data:{labels:labels,datasets:datasets},
    options:{
      responsive:true,
      maintainAspectRatio:false,
      plugins:{
        legend:{display:false},
        tooltip:{
          callbacks:{
            label:function(ctx){
              var v=ctx.parsed.y;
              return ctx.dataset.label+': '+(v===null?'N/A':v.toFixed(1)+'%');
            }
          }
        }
      },
      scales:{
        y:{title:{display:true,text:'失业率 (%)'},beginAtZero:false,grid:{color:'#e5e7eb'}},
        x:{grid:{display:false},ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:12}}
      }
    }
  });
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
    var precision=2;
    var m=i.fmt&&i.fmt.match(/\\{(\\d+)\\.(\\d+)f\\}/);
    if(m) precision=parseInt(m[2]);
    valStr=typeof i.value==='number'?i.value.toFixed(precision):(i.value||'—');
    var dateStr=i.date?'数据 '+i.date:'';
    var staleMsg=i.stale&&i.stale_reason?i.stale_reason:(i.stale?'stale':'');
    var srcStr=i.source?(i.source+(staleMsg?' ('+staleMsg+')':'')):'';
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
hyp_js_lines.append(unrate_js)

# 超大假设 (mega) section 渲染
hyp_js_lines.append('var MEGA_IDS=' + mega_json + ';')
hyp_js_lines.append('''
function renderMegas(){
  var q=(document.getElementById('megaSearch')||{}).value||'';
  q=q.trim().toLowerCase();
  var list=MEGA_IDS.map(function(id){return byId[id];}).filter(function(x){return x&&(!q||(x.title+' '+(x.rationale||'')).toLowerCase().indexOf(q)>=0)});
  var empty=document.getElementById('megaEmpty');
  var grid=document.getElementById('megaGrid');
  if(!grid)return;
  if(!list.length){if(empty)empty.style.display='block';grid.innerHTML='';return;}
  if(empty)empty.style.display='none';
  grid.innerHTML=list.map(function(m){
    var inds=(m.indicators||[]).map(function(i){
      return '<div class="mega-ind"><b>'+esc(i.name)+':</b> '+esc(i.current_value||'\u672a\u77e5')+' (\u652f\u6301\u9608\u503c '+esc(i.threshold_support||'-')+' / \u53cd\u9a73\u9608\u503c '+esc(i.threshold_refute||'-')+') <span style="color:var(--text-muted)">\u2014 '+esc(i.source||'')+'</span></div>';
    }).join('');
    return '<div class="mega-card">'
      +'<div class="mega-level">\U0001F310 \u8d85\u5927\u5047\u8bbe \u00b7 \u6218\u7565\u7ea7</div>'
      +'<div class="mega-conf">'+Math.round((m.confidence||0)*100)+'%</div>'
      +'<div class="mega-title">'+esc(m.title)+'</div>'
      +'<div class="mega-rationale">'+esc(m.rationale||'')+'</div>'
      +(inds?'<div class="mega-indicators"><b>\u9a8c\u8bc1\u6307\u6807</b>'+inds+'</div>':'')
      +'</div>';
  }).join('');
}
window.renderMegas=renderMegas;
renderMegas();
''')

# 情报流 section 折叠提示文字切换
hyp_js_lines.append('''
(function(){
  var sec=document.getElementById('intelSection');
  if(!sec)return;
  var hint=sec.querySelector('.section-toggle-hint');
  function upd(){if(hint)hint.textContent=sec.open?'点击收起 ▴':'点击展开 ▾';}
  sec.addEventListener('toggle',upd);
  upd();
})();
''')

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
function R(cat){var list=cat?D.filter(function(i){return i.category_cn===cat}):D;var sorted=list.slice();if(curSort==="time"){sorted.sort(function(a,b){return new Date(b.published_at||0)-new Date(a.published_at||0)})}else{sorted.sort(function(a,b){return (b.relevance||0)-(a.relevance||0)})}document.getElementById("il").innerHTML=sorted.map(function(i){var cc=i.category_cn||"other";var rv=i.relevance||0;var lang=(i.language||"en").toUpperCase();var pub=i.published_cn||"";if(!pub&&i.published_at){var pd=new Date(i.published_at);if(!isNaN(pd.getTime()))pub=pd.toLocaleDateString("zh-CN")}var ago=(function(){var pa=new Date(i.published_at||0);if(isNaN(pa.getTime()))return i.time_ago||"";var dm=(Date.now()-pa.getTime())/60000;if(dm<0)dm=0;return dm<1?"刚刚":dm<60?Math.round(dm)+"分钟前":dm<1440?Math.round(dm/60)+"小时前":Math.round(dm/1440)+"天前"})();var kf=(i.key_facts&&i.key_facts.length)?i.key_facts.join("; "):"";var hasOrig=i.content_full&&i.content_full.length>30;return '<div class="item"><div class="meta-row"><span class="cat cat-'+cc+'">'+cc+'</span><span class="badge">'+lang+'</span><span class="rel rel-'+rv+'">R'+rv+'</span><span class="src">'+(i.source_name||"")+'</span></div><div class="meta-row"><span class="time-tag">'+pub+'</span><span class="time-tag">'+ago+'</span></div><div class="item-title">'+esc(i.cn_title||i.title||"")+'</div><div class="item-body">'+esc(i.cn_summary||"")+'</div>'+(kf?'<div class="kf">关键事实: '+esc(kf)+'</div>':'')+(i.impact?'<div class="impact">👤 '+esc(i.impact)+'</div>':'')+(i.graduate_impact?'<div class="impact grad">🎓 '+esc(i.graduate_impact)+'</div>':'')+(i.dims?'<details class="dims"><summary>四维诊断</summary><div class="dims-body">'+Object.entries(i.dims).map(function(p){return '<div><b>'+({accumulation_node:'积累制度',spatial_layer:'空间修正',state_market_shift:'国家-市场',class_interest:'阶级利益'}[p[0]]||p[0])+'</b>：'+esc(p[1])+'</div>'}).join('')+'</div></details>':'')+(hasOrig?'<button class="expand-btn" onclick="tog(this)">展开原文</button><div class="orig">'+esc(i.content_full)+'</div>':'')+'</div>'}).join("")}
function S(s){curSort=s;document.getElementById("sort-relevance").classList.toggle("active",s==="relevance");document.getElementById("sort-time").classList.toggle("active",s==="time");R(curCat)}
function toggleChat(){var p=document.getElementById("chatPanel");p.classList.toggle("open");if(p.classList.contains("open"))document.getElementById("chatInput").focus()}
function pushMsg(cls,text){var box=document.getElementById("chatMsgs");var div=document.createElement("div");div.className="chat-msg "+cls;div.textContent=text;box.appendChild(div);box.scrollTop=box.scrollHeight}
async function sendChat(){var inp=document.getElementById("chatInput");var q=inp.value.trim();if(!q)return;inp.value="";pushMsg("user",q);var box=document.getElementById("chatMsgs");var tip=document.createElement("div");tip.className="chat-msg ai typing";tip.textContent="参谋长研判中……";box.appendChild(tip);box.scrollTop=box.scrollHeight;try{var r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({q:q})});var d=await r.json();tip.remove();pushMsg("ai",d.answer||("出错: "+(d.error||"未知错误")))}catch(e){tip.remove();pushMsg("ai","请求失败: "+e)}}
document.addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey&&document.activeElement===document.getElementById("chatInput")){e.preventDefault();sendChat()}});
function tog(btn){var c=btn.nextElementSibling;var open=c.style.display!=="block";c.style.display=open?"block":"none";btn.textContent=open?"收起原文":"展开原文"}
function esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
buildCatBtns();R("");""")
intel_js_lines.append('</script>')
intel_js_lines.append('<script>var _lastUpdate="' + generated_at + '";(function(){var el=document.getElementById("headerUpdated");if(!el)return;var dt=new Date(_lastUpdate);if(isNaN(dt.getTime())){el.textContent="最近更新: "+_lastUpdate;return;}var local=dt.toLocaleString("zh-CN",{hour12:false,timeZone:"Asia/Shanghai"});el.textContent="最近更新: "+local;el.title="UTC: "+_lastUpdate;})();</script>')

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

print(f"Dashboard generated: {len(html)} bytes")
print(f"Intelligence: {len(intel)}")
print(f"Hypotheses: {len(hyps)} (majors: {len(major_ids)})")
