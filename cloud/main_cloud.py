#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端情报官 - 主入口
GitHub Actions 运行此脚本，产出 intel_YYYYMMDD.jsonl
"""

import json
import yaml
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path


def run_fetcher(script: str, config: str, output: str):
    cmd = [sys.executable, script, config, output]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"[ERROR] {script} failed: {result.stderr}")
        return []
    
    items = []
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            try:
                items.append(json.loads(line))
            except:
                pass
    return items


def main():
    with open("sources.yaml", "r", encoding="utf-8") as f:
        sources_config = yaml.safe_load(f)
    
    with open("weights.yaml", "r", encoding="utf-8") as f:
        weights_config = yaml.safe_load(f)
    
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_file = f"intel_raw_{today}.jsonl"
    final_file = f"intel_{today}.jsonl"
    
    all_items = []
    
    print("=== 抓取 RSS 源 ===")
    rss_items = run_fetcher("cloud/fetch_rss.py", "sources.yaml", f"rss_{today}.jsonl")
    all_items.extend(rss_items)
    
    print("=== 抓取列表页源 ===")
    list_items = run_fetcher("cloud/fetch_list.py", "sources.yaml", f"list_{today}.jsonl")
    all_items.extend(list_items)
    
    print(f"=== 合计原始条目: {len(all_items)} ===")
    
    with open(raw_file, "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    print("=== 运行清洗/去重/评分管道 ===")
    from cloud.clean_dedup_score import pipeline
    final_items = pipeline(raw_file, final_file, "weights.yaml")
    
    summary = {
        "date": today,
        "total_raw": len(all_items),
        "total_final": len(final_items),
        "by_category": {},
        "by_priority": {},
        "top_keywords": {}
    }
    
    for item in final_items:
        cat = item.get("category", "unknown")
        summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
        pri = item.get("priority", "unknown")
        summary["by_priority"][pri] = summary["by_priority"].get(pri, 0) + 1
        for kw in item.get("keywords_hit", []):
            summary["top_keywords"][kw] = summary["top_keywords"].get(kw, 0) + 1
    
    with open(f"intel_summary_{today}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"=== 完成 ===")
    print(f"输出文件: {final_file}")
    print(f"摘要: {summary}")
    
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"intel_file={final_file}\n")
            f.write(f"summary_file=intel_summary_{today}.json\n")


if __name__ == "__main__":
    main()
