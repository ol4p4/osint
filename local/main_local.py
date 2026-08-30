#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 主入口
双击运行：加载情报 -> AI深度分析 -> 生成三形态产物 -> 入库
"""

import sys
import os
import yaml
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from load_intel import load_intel, load_persona
from load_knowledge import load_knowledge
from analyze import analyze_intel
from render_brief import render_brief
from render_dashboard import render_dashboard
from render_wiki import render_wiki


def main():
    print("=" * 60)
    print("本地参谋长 - OSINT 个人智库系统")
    print("=" * 60)
    
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    
    paths = config.get("paths", {})
    output_dir = paths.get("output_dir", r"D:\osint\data")
    cache_dir = paths.get("intel_cache_dir", r"D:\osint\data\cache")
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir, "wiki").mkdir(parents=True, exist_ok=True)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir, "logs").mkdir(parents=True, exist_ok=True)
    
    print("\n[1/6] 加载用户画像...")
    persona_path = Path(__file__).parent.parent / "persona.md"
    persona = load_persona(str(persona_path))
    print(f"    画像加载完成 ({len(persona)} 字符)")
    
    print("\n[2/6] 加载知识库...")
    vault_path = config.get("knowledge_base", {}).get("vault_path", r"D:\Codex输出\视频知识库")
    kb = load_knowledge(vault_path)
    idx = kb.load_all()
    nodes_count = idx["total_nodes"]
    macro_count = len(idx["macro_concepts"])
    print(f"    知识库加载完成: {nodes_count} 个节点")
    print(f"    宏观概念: {macro_count} 个")
    
    print("\n[3/6] 加载情报数据...")
    intel_items = load_intel(config)
    if not intel_items:
        print("    未找到情报数据，尝试从缓存加载...")
        cache_files = list(Path(cache_dir).glob("intel_*.jsonl"))
        if cache_files:
            latest = max(cache_files, key=lambda p: p.stat().st_mtime)
            print(f"    找到缓存: {latest}")
            for line in latest.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    intel_items.append(json.loads(line))
    
    if not intel_items:
        print("    无可用情报数据，退出")
        return 1
    
    intel_count = len(intel_items)
    print(f"    加载情报: {intel_count} 条")
    
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    cache_file = Path(cache_dir) / f"intel_{date_str}.jsonl"
    cache_file.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in intel_items) + "\n",
        encoding="utf-8")
    print(f"    缓存已更新: {cache_file}")
    
    print("\n[4/6] AI 深度分析（四维政治经济学框架）...")
    analyses = analyze_intel(config, persona, kb, intel_items)
    analysis_count = len(analyses)
    print(f"    分析完成: {analysis_count} 条")
    
    analysis_file = Path(output_dir) / f"analysis_{date_str}.jsonl"
    analysis_file.write_text(
        "\n".join(json.dumps(a.__dict__ if hasattr(a, "__dict__") else a, ensure_ascii=False) for a in analyses) + "\n",
        encoding="utf-8")
    print(f"    分析结果已保存: {analysis_file}")
    
    print("\n[5/6] 生成产物...")
    
    brief_cfg = config.get("output", {}).get("brief", {})
    if brief_cfg.get("enabled", True):
        print("    生成每日简报...")
        render_brief(analyses, intel_items, date_str, output_dir, config)
    
    dash_cfg = config.get("output", {}).get("dashboard", {})
    if dash_cfg.get("enabled", True):
        print("    生成交互式仪表盘...")
        render_dashboard(analyses, intel_items, date_str, output_dir, config)
    
    wiki_cfg = config.get("output", {}).get("wiki", {})
    if wiki_cfg.get("enabled", True):
        print("    生成 Obsidian 知识库页面...")
        render_wiki(analyses, intel_items, date_str, config)
    
    print("\n[6/6] 完成!")
    print("=" * 60)
    print(f"输出目录: {output_dir}")
    print(f"  简报: brief_{date_str}.md")
    print(f"  仪表盘: dashboard_{date_str}.html")
    print(f"  知识库: wiki/osint-{date_str}.md + 宏观概念页")
    print(f"  分析明细: analysis_{date_str}.jsonl")
    print("=" * 60)
    print("\n使用建议:")
    print("  1. 双击 brief_*.md 在 Obsidian/浏览器阅读每日内参")
    print("  2. 双击 dashboard_*.html 打开交互式仪表盘筛选钻取")
    print("  3. 在 Obsidian 中查看 wiki/ 目录下的新页面")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
