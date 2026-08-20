#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 每日简报生成器
"""

import json
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path
from dataclasses import asdict


def render_brief(analyses, intel_items: List[Dict], date_str: str, output_dir: str, config: Dict) -> str:
    # 兼容 dataclass
    analyses = [asdict(a) if hasattr(a, "__dataclass_fields__") else a for a in analyses]
    
    high = [a for a in analyses if a.get("confidence", 0) >= 7]
    medium = [a for a in analyses if 4 <= a.get("confidence", 0) < 7]
    low = [a for a in analyses if a.get("confidence", 0) < 4]
    
    intel_map = {item["id"]: item for item in intel_items}
    
    lines = []
    lines.append(f"# 每日内参 {date_str}")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 情报条数：{len(intel_items)} | 深度分析：{len(analyses)}")
    lines.append("")
    
    lines.append("## 执行摘要")
    if high:
        lines.append(f"🔴 **高置信度（{len(high)}条）**：核心结构性信号，需优先决策")
    if medium:
        lines.append(f"🟡 **中置信度（{len(medium)}条）**：重要趋势信号，建议跟踪")
    if low:
        lines.append(f"🟢 **低置信度（{len(low)}条）**：背景信息，仅作参考")
    lines.append("")
    
    lines.append("## 核心结构性判断")
    structural_themes = {}
    for a in high + medium:
        for key, val in a.get("macro_diagnosis", {}).items():
            if val and val != "未识别":
                structural_themes.setdefault(key, []).append(val)
    for theme, vals in structural_themes.items():
        unique_vals = list(set(vals))
        joined = "; ".join(unique_vals[:3])
        lines.append(f"- **{theme}**：{joined}")
    lines.append("")
    
    if high:
        lines.append("## 🔴 高置信度情报深度研判")
        for a in high:
            intel = intel_map.get(a["intel_id"], {})
            _render_single(lines, a, intel, "high")
    
    if medium:
        lines.append("## 🟡 中置信度情报跟踪")
        for a in medium:
            intel = intel_map.get(a["intel_id"], {})
            _render_single(lines, a, intel, "medium")
    
    if low:
        lines.append("## 🟢 低置信度情报归档")
        for a in low:
            intel = intel_map.get(a["intel_id"], {})
            title = intel.get("title", a["intel_id"])
            source = intel.get("source_name", "")
            impl = a.get("structural_implication", "")[:120]
            conf = a.get("confidence", 0)
            lines.append(f"- **{title}** ({source})")
            lines.append(f"  - 结构判断：{impl}...")
            lines.append(f"  - 置信度：{conf}/10")
            lines.append("")
    
    lines.append("## 行动清单汇总")
    all_moves, all_traps, all_signals = [], [], []
    for a in high + medium:
        pas = a.get("personal_action_space", {})
        all_moves.extend(pas.get("concrete_moves", []))
        all_traps.extend(pas.get("avoid_traps", []))
        all_signals.extend(pas.get("signals_to_watch", []))
    
    seen = set()
    unique_moves = []
    for m in all_moves:
        key = m.get("action", "")[:50]
        if key not in seen:
            seen.add(key)
            unique_moves.append(m)
    
    lines.append("### 推荐行动")
    for i, m in enumerate(unique_moves[:8], 1):
        lines.append(f"{i}. **{m.get('action', '')}**")
        lines.append(f"   - 理由：{m.get('rationale', '')}")
        lines.append(f"   - 风险：{m.get('risk', '')}")
    
    lines.append("")
    lines.append("### 避坑指南")
    for trap in list(set(all_traps))[:6]:
        lines.append(f"- {trap}")
    
    lines.append("")
    lines.append("### 关键观测信号")
    for sig in list(set(all_signals))[:8]:
        lines.append(f"- {sig}")
    
    lines.append("")
    lines.append("---")
    lines.append("*本简报由 OSINT 个人智库系统自动生成*")
    
    output_path = Path(output_dir) / f"brief_{date_str}.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[RENDER] 简报生成: {output_path}")
    return str(output_path)


def _render_single(lines, analysis, intel, priority):
    icon = "🔴" if priority == "high" else "🟡"
    title = intel.get("title", analysis["intel_id"])
    source = intel.get("source_name", "")
    score = analysis.get("confidence", 0)
    
    lines.append(f"### {icon} {title}")
    lines.append(f"> 来源：{source} | 置信度：{score}/10 | 时间：{intel.get('published_at', '')[:10]}")
    lines.append("")
    
    lines.append("**四维结构诊断**")
    md = analysis.get("macro_diagnosis", {})
    lines.append(f"- 积累环节：{md.get('accumulation_node', '未识别')}")
    lines.append(f"- 空间层级：{md.get('spatial_layer', '未识别')}")
    lines.append(f"- 国家-市场：{md.get('state_market_shift', '未识别')}")
    lines.append(f"- 利益集团：{md.get('class_interest', '未识别')}")
    lines.append("")
    
    lines.append("**结构性含义**")
    lines.append(analysis.get("structural_implication", "无"))
    lines.append("")
    
    pas = analysis.get("personal_action_space", {})
    wm = pas.get("window_months", 18)
    lines.append(f"**行动空间（{wm}个月窗口）**")
    for m in pas.get("concrete_moves", []):
        lines.append(f"- ✅ {m.get('action', '')}")
        lines.append(f"  - 理由：{m.get('rationale', '')}")
        lines.append(f"  - 风险：{m.get('risk', '')}")
    
    if pas.get("avoid_traps"):
        joined = "；".join(pas["avoid_traps"])
        lines.append(f"- ⚠️ 避坑：{joined}")
    if pas.get("signals_to_watch"):
        joined = "；".join(pas["signals_to_watch"])
        lines.append(f"- 👁️ 观测：{joined}")
    if analysis.get("knowledge_links"):
        joined = "、".join(analysis["knowledge_links"])
        lines.append(f"- 🔗 知识库：{joined}")
    lines.append("")
    
    if analysis.get("contradictions"):
        lines.append(f"> ⚠️ 待验证：{analysis['contradictions']}")
    lines.append("")
