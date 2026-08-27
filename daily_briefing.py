# -*- coding: utf-8 -*-
"""
每日简报生成器
分析当天情报对各假设的影响，输出结构化简报
"""
import json, re, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

HYP_FILE = Path(r"D:\Codex输出\osint_卫星图\hypotheses\active_hypotheses.json")
INTEL_DIR = Path(r"D:\Codex输出\osint_卫星图")
LINK_REPORT = Path(r"D:\Codex输出\osint_卫星图\link_report.json")
BRIEFING_DIR = Path(r"D:\Codex输出\osint_卫星图")

def load_today_intel():
    """Load today's intelligence"""
    today = datetime.now().strftime("%Y%m%d")
    intel_file = INTEL_DIR / f"intel_{today}.jsonl"
    if not intel_file.exists():
        # Try latest file
        files = sorted(INTEL_DIR.glob("intel_*.jsonl"), reverse=True)
        if files:
            intel_file = files[0]
        else:
            return []
    
    items = []
    with open(intel_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except:
                    pass
    return items

def load_link_report():
    """Load the intel-hypothesis link report"""
    if LINK_REPORT.exists():
        with open(LINK_REPORT, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def generate_briefing(intel_items, hyps, link_report):
    """Generate structured daily briefing"""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    # Categorize intel
    categories = {}
    for item in intel_items:
        cat = item.get("category_cn", "other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)
    
    # Find hypothesis changes
    hyp_changes = []
    for hyp in hyps:
        if hyp.get("level") != "small":
            continue
        evidence = hyp.get("evidence_log", [])
        today_evidence = [e for e in evidence if e.get("date") == today_str]
        if today_evidence:
            hyp_changes.append({
                "id": hyp["id"],
                "title": hyp["title"],
                "parent": hyp.get("parent", ""),
                "confidence": hyp.get("confidence", 0),
                "new_evidence": len(today_evidence),
                "evidence_summary": [e.get("summary", "")[:60] for e in today_evidence[:3]]
            })
    
    # Build briefing
    briefing = []
    briefing.append(f"# 每日情报简报 - {today_str}")
    briefing.append(f"")
    briefing.append(f"**情报总数**: {len(intel_items)} 条")
    briefing.append(f"**分类分布**: {', '.join(f'{k}({len(v)})' for k, v in sorted(categories.items(), key=lambda x: -len(x[1])))}")
    briefing.append(f"")
    
    # Top stories
    briefing.append(f"## 今日重点情报")
    briefing.append(f"")
    top_items = sorted(intel_items, key=lambda x: x.get("relevance", 0), reverse=True)[:10]
    for i, item in enumerate(top_items, 1):
        title = item.get("cn_title", "") or item.get("title", "")
        cat = item.get("category_cn", "")
        impact = item.get("impact", "")
        source = item.get("source_name", "")
        time_str = item.get("published_cn", "")
        briefing.append(f"### {i}. [{cat}] {title}")
        briefing.append(f"- **来源**: {source} | **时间**: {time_str}")
        briefing.append(f"- **影响**: {impact}")
        briefing.append(f"")
    
    # Hypothesis updates
    briefing.append(f"## 假设状态更新")
    briefing.append(f"")
    if hyp_changes:
        for change in hyp_changes:
            briefing.append(f"### {change['title']}")
            briefing.append(f"- **置信度**: {change['confidence']:.0%}")
            briefing.append(f"- **新证据**: {change['new_evidence']} 条")
            for ev in change["evidence_summary"]:
                briefing.append(f"  - {ev}")
            briefing.append(f"")
    else:
        briefing.append(f"今日无假设状态变化。")
        briefing.append(f"")
    
    # Confidence ranking
    briefing.append(f"## 假设置信度排行")
    briefing.append(f"")
    small_hyps = [h for h in hyps if h.get("level") == "small"]
    small_hyps.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    
    briefing.append(f"| 排名 | 假设 | 置信度 | 方向 |")
    briefing.append(f"|------|------|--------|------|")
    for i, h in enumerate(small_hyps[:15], 1):
        conf = h.get("confidence", 0)
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        briefing.append(f"| {i} | {h['title'][:40]} | {conf:.0%} {bar} | {h.get('direction', '')} |")
    briefing.append(f"")
    
    # Key signals
    briefing.append(f"## 关键信号")
    briefing.append(f"")
    signals = []
    for item in intel_items:
        if item.get("relevance", 0) >= 5:
            signals.append(f"- **{item.get('cn_title', '') or item.get('title', '')}** ({item.get('source_name', '')})")
    for s in signals[:10]:
        briefing.append(s)
    briefing.append(f"")
    
    briefing.append(f"---")
    briefing.append(f"*生成时间: {now.strftime('%Y-%m-%d %H:%M')}*")
    
    return "\n".join(briefing)

def main():
    print("=== 每日简报生成器 ===")
    
    # Load data
    intel_items = load_today_intel()
    print(f"加载情报: {len(intel_items)} 条")
    
    with open(HYP_FILE, "r", encoding="utf-8") as f:
        hyps = json.load(f)
    print(f"加载假设: {len(hyps)} 条")
    
    link_report = load_link_report()
    
    # Generate briefing
    briefing = generate_briefing(intel_items, hyps, link_report)
    
    # Save briefing
    today = datetime.now().strftime("%Y-%m-%d")
    briefing_file = BRIEFING_DIR / f"briefing_{today}.md"
    with open(briefing_file, "w", encoding="utf-8") as f:
        f.write(briefing)
    
    # Also save to analysis directory
    analysis_file = INTEL_DIR / f"analysis_{today}.md"
    with open(analysis_file, "w", encoding="utf-8") as f:
        f.write(briefing)
    
    print(f"\n=== 完成 ===")
    print(f"简报保存: {briefing_file}")
    print(f"分析保存: {analysis_file}")
    print(f"\n--- 简报预览 ---")
    print(briefing[:2000])

if __name__ == "__main__":
    main()
