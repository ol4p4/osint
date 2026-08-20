#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 交互式仪表盘生成器
输出单文件 HTML，包含 Alpine.js + Chart.js，可筛选/钻取
"""

import json
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path


from dataclasses import asdict
def render_dashboard(analyses, intel_items: List[Dict], date_str: str, output_dir: str, config: Dict) -> str:
    from dataclasses import asdict
    analyses = [asdict(a) if hasattr(a, "__dataclass_fields__") else a for a in analyses]
    # 优先级映射
    for a in analyses:
        score = a.get("confidence", 0)
        if score >= 7:
            a["priority"] = "high"
        elif score >= 4:
            a["priority"] = "medium"
        else:
            a["priority"] = "low"
    
    intel_map = {item["id"]: item for item in intel_items}
    
    analyses_json = json.dumps(analyses, ensure_ascii=False)
    intel_map_json = json.dumps(intel_map, ensure_ascii=False)
    
    html = DASHBOARD_HTML.format(
        date_str=date_str,
        total_intel=len(intel_items),
        total_analysis=len(analyses),
        analyses_json=analyses_json,
        intel_map_json=intel_map_json
    )
    
    output_path = Path(output_dir) / f"dashboard_{date_str}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[RENDER] 仪表盘生成: {output_path}")
    
    return str(output_path)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OSINT 个人智库仪表盘 - {{date_str}}</title>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
  --fg: #e6edf3; --fg2: #8b949e; --accent: #58a6ff;
  --border: #30363d; --red: #f85149; --yellow: #d29922; --green: #3fb950; --purple: #a371f7;
}}
body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--fg); line-height: 1.6; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
header {{ margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
h1 {{ font-size: 1.75rem; }}
.meta {{ color: var(--fg2); font-size: 0.9rem; margin-top: 4px; }}
.filters {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; padding: 16px; background: var(--bg2); border-radius: 8px; border: 1px solid var(--border); }}
.fg {{ display: flex; align-items: center; gap: 8px; }}
.fg label {{ font-size: 0.85rem; color: var(--fg2); }}
select {{ padding: 6px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; color: var(--fg); font-size: 0.85rem; }}
.stat-pills {{ display: flex; gap: 8px; margin-left: auto; }}
.pill {{ padding: 4px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 999px; font-size: 0.75rem; color: var(--fg2); }}
.pill.h {{ border-color: var(--red); color: var(--red); }}
.pill.m {{ border-color: var(--yellow); color: var(--yellow); }}
.pill.l {{ border-color: var(--green); color: var(--green); }}
.charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.cc {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
.cc h3 {{ font-size: 0.95rem; margin-bottom: 12px; }}
.cw {{ position: relative; height: 280px; }}
.list {{ display: flex; flex-direction: column; gap: 12px; }}
.card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; transition: border-color 0.2s; }}
.card:hover {{ border-color: var(--accent); }}
.card.high {{ border-left: 4px solid var(--red); }}
.card.medium {{ border-left: 4px solid var(--yellow); }}
.card.low {{ border-left: 4px solid var(--green); }}
.ch {{ padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }}
.ct {{ font-weight: 500; font-size: 0.95rem; flex: 1; }}
.badges {{ display: flex; gap: 6px; }}
.b {{ padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; }}
.bsrc {{ background: var(--bg3); color: var(--fg2); }}
.bh {{ background: rgba(248,81,73,0.15); color: var(--red); }}
.bm {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
.bl {{ background: rgba(63,185,80,0.15); color: var(--green); }}
.bd {{ background: rgba(163,113,247,0.15); color: var(--purple); font-size: 0.65rem; }}
.cb {{ padding: 0 16px 16px; border-top: 1px solid var(--border); display: none; }}
.cb.open {{ display: block; }}
.dr {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; font-size: 0.8rem; }}
.di {{ padding: 8px; background: var(--bg3); border-radius: 6px; }}
.dl {{ color: var(--fg2); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }}
.dv {{ font-weight: 500; margin-top: 2px; }}
.sec {{ margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }}
.st {{ font-size: 0.8rem; font-weight: 600; color: var(--fg2); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
.al {{ display: flex; flex-direction: column; gap: 6px; }}
.ai {{ padding: 8px 10px; background: var(--bg3); border-radius: 6px; font-size: 0.8rem; }}
.ar {{ color: var(--fg2); font-size: 0.75rem; margin-top: 2px; }}
.aw {{ color: var(--red); font-size: 0.75rem; margin-top: 2px; }}
.ll {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.lt {{ padding: 2px 8px; background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; font-size: 0.75rem; color: var(--accent); }}
.reasoning {{ font-size: 0.75rem; color: var(--fg2); background: var(--bg3); padding: 10px; border-radius: 6px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }}
@media (max-width: 768px) {{ .dr {{ grid-template-columns: repeat(2, 1fr); }} .charts {{ grid-template-columns: 1fr; }} .filters {{ flex-direction: column; }} }}
</style>
</head>
<body>
<div class="container" x-data="db()">
<header>
<h1>OSINT 个人智库仪表盘</h1>
<div class="meta">{{date_str}} 生成 | {{total_intel}} 条情报 | {{total_analysis}} 条深度分析</div>
</header>
<div class="filters">
<div class="fg"><label>来源</label>
<select x-model="f.src" @change="ap()"><option value="">全部</option><template x-for="s in us()" :key="s"><option :value="s" x-text="s"></option></template></select></div>
<div class="fg"><label>优先级</label>
<select x-model="f.pri" @change="ap()"><option value="">全部</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></div>
<div class="fg"><label>积累环节</label>
<select x-model="f.acc" @change="ap()"><option value="">全部</option><option value="生产">生产</option><option value="实现">实现</option><option value="分配">分配</option><option value="再生产">再生产</option></select></div>
<div class="fg"><label>空间层级</label>
<select x-model="f.spa" @change="ap()"><option value="">全部</option><option value="中心">中心</option><option value="外围">外围</option><option value="特区">特区</option><option value="都市圈">都市圈</option></select></div>
<div class="fg"><label>国家-市场</label>
<select x-model="f.sm" @change="ap()"><option value="">全部</option><option value="国家进场">国家进场</option><option value="市场退场">市场退场</option><option value="边界模糊">边界模糊</option><option value="试点先行">试点先行</option></select></div>
<div class="fg"><label>搜索</label>
<input type="text" x-model="f.kw" @input="dap()" placeholder="标题/关键词..." style="padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--fg);min-width:160px;"></div>
<div class="stat-pills">
<span class="pill h" x-text="'高:'+st().h"></span>
<span class="pill m" x-text="'中:'+st().m"></span>
<span class="pill l" x-text="'低:'+st().l"></span>
</div>
</div>
<div class="charts">
<div class="cc"><h3>优先级分布</h3><div class="cw"><canvas id="cPri"></canvas></div></div>
<div class="cc"><h3>来源分布</h3><div class="cw"><canvas id="cSrc"></canvas></div></div>
<div class="cc"><h3>积累环节</h3><div class="cw"><canvas id="cAcc"></canvas></div></div>
<div class="cc"><h3>空间层级</h3><div class="cw"><canvas id="cSpa"></canvas></div></div>
<div class="cc"><h3>国家-市场边界</h3><div class="cw"><canvas id="cSM"></canvas></div></div>
<div class="cc"><h3>利益集团</h3><div class="cw"><canvas id="cCls"></canvas></div></div>
</div>
<div class="list" x-show="fa().length>0">
<template x-for="a in fa()" :key="a.intel_id">
<div class="card" :class="a.priority">
<div class="ch" @click="tc(a.intel_id)">
<span class="ct" x-text="it(a.intel_id)"></span>
<div class="badges">
<span class="b bsrc" x-text="is(a.intel_id)"></span>
<span class="b" :class="a.priority==='high'?'bh':a.priority==='medium'?'bm':'bl'" x-text="a.priority==='high'?'高':a.priority==='medium'?'中':'低'"></span>
<span class="b bd" x-text="a.macro_diagnosis.accumulation_node"></span>
<span class="b bd" x-text="a.macro_diagnosis.spatial_layer"></span>
<span class="b bd" x-text="a.macro_diagnosis.state_market_shift"></span>
<span x-text="oc.includes(a.intel_id)?'▲':'▼'" style="color:var(--fg2);margin-left:8px;"></span>
</div>
</div>
<div class="cb" :class="{{open:oc.includes(a.intel_id)}}">
<div class="dr">
<div class="di"><div class="dl">积累环节</div><div class="dv" x-text="a.macro_diagnosis.accumulation_node"></div></div>
<div class="di"><div class="dl">空间层级</div><div class="dv" x-text="a.macro_diagnosis.spatial_layer"></div></div>
<div class="di"><div class="dl">国家-市场</div><div class="dv" x-text="a.macro_diagnosis.state_market_shift"></div></div>
<div class="di"><div class="dl">利益集团</div><div class="dv" x-text="a.macro_diagnosis.class_interest"></div></div>
</div>
<div class="sec"><div class="st">结构性含义</div><div x-text="a.structural_implication"></div></div>
<div class="sec" x-show="a.personal_action_space.concrete_moves?.length>0">
<div class="st" x-text="'推荐行动 ('+a.personal_action_space.window_months+'个月窗口)'"></div>
<div class="al">
<template x-for="m in a.personal_action_space.concrete_moves" :key="m.action">
<div class="ai"><div x-text="m.action"></div><div class="ar" x-text="m.rationale"></div><div class="aw" x-show="m.risk" x-text="'风险: '+m.risk"></div></div>
</template>
</div></div>
<div class="sec" x-show="a.personal_action_space.avoid_traps?.length>0">
<div class="st">避坑指南</div>
<div class="al"><template x-for="t in a.personal_action_space.avoid_traps" :key="t"><div class="ai" style="color:var(--red);" x-text="'✕ '+t"></div></template></div></div>
<div class="sec" x-show="a.personal_action_space.signals_to_watch?.length>0">
<div class="st">关键观测信号</div>
<div class="ll"><template x-for="s in a.personal_action_space.signals_to_watch" :key="s"><span class="lt" x-text="s"></span></template></div></div>
<div class="sec" x-show="a.knowledge_links?.length>0">
<div class="st">知识库关联</div>
<div class="ll"><template x-for="l in a.knowledge_links" :key="l"><span class="lt" x-text="l"></span></template></div></div>
<div class="sec"><div class="st" x-text="'置信度: '+a.confidence+'/10 | 待验证'"></div>
<div class="reasoning" x-text="a.contradictions"></div></div>
<div class="sec" x-show="a.raw_reasoning"><div class="st">AI 推理链</div>
<div class="reasoning" x-text="a.raw_reasoning"></div></div>
</div>
</div>
</template>
</div>
</div>
<script>
const A={{analyses_json}};
const M={{intel_map_json}};
function db(){{return{{
  analyses:A,intelMap:M,f:{{src:"",pri:"",acc:"",spa:"",sm:"",kw:""}},
  oc:[],
  fa(){{let r=this.analyses;
    if(this.f.src)r=r.filter(a=>this.is(a.intel_id)===this.f.src);
    if(this.f.pri)r=r.filter(a=>a.priority===this.f.pri);
    if(this.f.acc)r=r.filter(a=>a.macro_diagnosis?.accumulation_node===this.f.acc);
    if(this.f.spa)r=r.filter(a=>a.macro_diagnosis?.spatial_layer===this.f.spa);
    if(this.f.sm)r=r.filter(a=>a.macro_diagnosis?.state_market_shift===this.f.sm);
    if(this.f.kw){{const k=this.f.kw.toLowerCase();r=r.filter(a=>this.it(a.intel_id).toLowerCase().includes(k)||(this.intelMap[a.intel_id]?.keywords_hit||[]).join(" ").toLowerCase().includes(k));}}
    return r;
  }},
  us(){{return[...new Set(this.analyses.map(a=>this.is(a.intel_id)).filter(Boolean))].sort();}},
  st(){{return{{h:this.analyses.filter(a=>a.priority==="high").length,m:this.analyses.filter(a=>a.priority==="medium").length,l:this.analyses.filter(a=>a.priority==="low").length}}}},
  it(id){{return this.intelMap[id]?.title||id;}},
  is(id){{return this.intelMap[id]?.source_name||"";}},
  tc(id){{const i=this.oc.indexOf(id);if(i>=0)this.oc.splice(i,1);else this.oc.push(id);}},
  ap(){{this.oc=[];}},
  dap(){{clearTimeout(this._t);this._t=setTimeout(()=>this.ap(),300);}},
  initCharts(){{const C=[["#f85149","#d29922","#3fb950"]];
    new Chart(document.getElementById("cPri"),{{type:"doughnut",data:{{labels:["高","中","低"],datasets:[{{data:[this.st().h,this.st().m,this.st().l],backgroundColor:C[0],borderWidth:0}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:"bottom",labels:{{color:"#8b949e",font:{{size:11}}}}}}}}}}}});
    const sc={{}};this.analyses.forEach(a=>{{const s=this.is(a.intel_id);sc[s]=(sc[s]||0)+1;}});
    new Chart(document.getElementById("cSrc"),{{type:"bar",data:{{labels:Object.keys(sc),datasets:[{{label:"数量",data:Object.values(sc),backgroundColor:"#58a6ff"}}]}},options:{{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:"#8b949e"}},grid:{{color:"#30363d"}}}},y:{{ticks:{{color:"#8b949e"}},grid:{{display:false}}}}}}}}}});
    const ac={{}};this.analyses.forEach(a=>{{const v=a.macro_diagnosis?.accumulation_node;if(v)ac[v]=(ac[v]||0)+1;}});
    new Chart(document.getElementById("cAcc"),{{type:"pie",data:{{labels:Object.keys(ac),datasets:[{{data:Object.values(ac),backgroundColor:["#58a6ff","#a371f7","#f85149","#3fb950"]}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:"bottom",labels:{{color:"#8b949e",font:{{size:10}}}}}}}}}}}});
    const sp={{}};this.analyses.forEach(a=>{{const v=a.macro_diagnosis?.spatial_layer;if(v)sp[v]=(sp[v]||0)+1;}});
    new Chart(document.getElementById("cSpa"),{{type:"pie",data:{{labels:Object.keys(sp),datasets:[{{data:Object.values(sp),backgroundColor:["#f85149","#d29922","#a371f7","#58a6ff"]}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:"bottom",labels:{{color:"#8b949e",font:{{size:10}}}}}}}}}}}});
    const sm={{}};this.analyses.forEach(a=>{{const v=a.macro_diagnosis?.state_market_shift;if(v)sm[v]=(sm[v]||0)+1;}});
    new Chart(document.getElementById("cSM"),{{type:"pie",data:{{labels:Object.keys(sm),datasets:[{{data:Object.values(sm),backgroundColor:["#3fb950","#f85149","#d29922","#a371f7"]}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:"bottom",labels:{{color:"#8b949e",font:{{size:10}}}}}}}}}}}});
    const ck=["国企","央企","民企","金融","地方政府","专业服务","蓝领","白领","青年劳动力"];
    const cc2={{}};this.analyses.forEach(a=>{{const t=(a.macro_diagnosis?.class_interest||"").toLowerCase();ck.forEach(k=>{{if(t.includes(k))cc2[k]=(cc2[k]||0)+1;}});}});
    new Chart(document.getElementById("cCls"),{{type:"bar",data:{{labels:Object.keys(cc2),datasets:[{{label:"提及",data:Object.values(cc2),backgroundColor:"#a371f7"}}]}},options:{{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:"#8b949e"}},grid:{{color:"#30363d"}}}},y:{{ticks:{{color:"#8b949e"}},grid:{{display:false}}}}}}}}}});
  }},
  init(){{this.$nextTick(()=>this.initCharts());}}
}}}}
</script>
</body>
</html>"""


def generate_dashboard_from_files(analysis_file: str, intel_file: str, output_dir: str, config: Dict):
    analyses = []
    with open(analysis_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                analyses.append(json.loads(line))
    intel_items = []
    with open(intel_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                intel_items.append(json.loads(line))
    date_str = datetime.now().strftime("%Y%m%d")
    return render_dashboard(analyses, intel_items, date_str, output_dir, config)


if __name__ == "__main__":
    import sys, argparse, yaml
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--intel", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    generate_dashboard_from_files(args.analysis, args.intel, args.output_dir, config)
