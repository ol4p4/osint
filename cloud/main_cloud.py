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


def run_fetcher(script, config, output):
    """运行采集脚本，从输出文件读取结果"""
    cmd = [sys.executable, script, config, output]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    
    if result.returncode != 0:
        print(f"[WARN] {script} exit code {result.returncode}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:200]}")
    
    # Print stdout logs
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                print(f"  {line}")
    
    # Read items from output file
    items = []
    output_path = Path(output)
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except:
                        pass
    
    return items


def main():
    # Ensure project root is on sys.path for cloud module imports
    import sys as _sys
    _project_root = str(Path(__file__).resolve().parent.parent)
    if _project_root not in _sys.path:
        _sys.path.insert(0, _project_root)
    with open("sources.yaml", "r", encoding="utf-8") as f:
        sources_config = yaml.safe_load(f)
    
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_file = f"intel_raw_{today}.jsonl"
    final_file = f"intel_{today}.jsonl"
    
    all_items = []
    
    print("=== Collecting RSS ===")
    rss_items = run_fetcher("cloud/fetch_rss.py", "sources.yaml", f"rss_{today}.jsonl")
    all_items.extend(rss_items)
    print(f"  RSS: {len(rss_items)} items")
    
    print("=== Collecting List pages ===")
    list_items = run_fetcher("cloud/fetch_list.py", "sources.yaml", f"list_{today}.jsonl")
    all_items.extend(list_items)
    print(f"  List: {len(list_items)} items")
    
    print(f"=== Total raw: {len(all_items)} ===")
    
    if not all_items:
        print("[ERROR] No items collected, aborting")
        sys.exit(1)
    
    with open(raw_file, "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    print("=== Running dedup + scoring ===")
    sys.stdout.flush()
    try:
        from cloud.clean_dedup_score import pipeline
        final_items = pipeline(raw_file, final_file, "sources.yaml")
    except Exception as e:
        print(f"[ERROR] Dedup pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        final_items = all_items
    
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
    
    print(f"=== Done ===")
    print(f"  Raw: {len(all_items)} -> Final: {len(final_items)}")
    print(f"  Output: {final_file}")
    
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"intel_file={final_file}\n")
            f.write(f"summary_file=intel_summary_{today}.json\n")
    



if __name__ == "__main__":
    main()
