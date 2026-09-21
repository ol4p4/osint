#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周循环入口（2026-09-21 新增）

背景：daily_run.ps1 原以 `python -c "...run_weekly_cycle()"` 单行调用，
未传 intel_items，导致 _save_ai_weekly_summary 的 week_intel 恒为空，
AI 周报连续两周写「本周情报总条数为 0」——库里实际有 4.5 万条情报。
（9-14 那份周报里 AI 还把「情报空白」解读成「最强信号」，
正是反幻觉护栏要防的情况。）

本脚本显式加载当日情报并传入周循环，同时把加载口径与 main_local 对齐
（优先产物目录当日文件，内容超期则拒绝，避免用陈旧数据糊周报）。
"""

import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import yaml

from analyze import MacroAnalyzer
from load_knowledge import load_knowledge
from hypothesis_engine import HypothesisEngine


def load_week_intel(output_dir, max_content_age_days=3, min_fresh_items=5):
    """加载当日情报供周报统计。

    与 main_local 同口径：产物目录当日文件优先（CI + 本地采集的合并结果）。
    新鲜度用「近 N 天条目数」判定，不用「最新一条距今几天」——后者会被
    源站错误时间戳的未来条目（实测有 2026-11-17）绕过，年龄算出负数。
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = Path(output_dir) / f"intel_{today}.jsonl"
    if not target.exists() or target.stat().st_size == 0:
        print(f"[weekly] 当日情报文件不存在: {target.name}")
        return []

    items = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lo = (now - timedelta(days=max_content_age_days)).strftime("%Y-%m-%d")
    hi = now.strftime("%Y-%m-%d")
    fresh = [i for i in items if lo <= str(i.get("published_at") or "")[:10] <= hi]
    if len(fresh) < min_fresh_items:
        print(f"[weekly] 近 {max_content_age_days} 天仅 {len(fresh)} 条情报"
              f"（下限 {min_fresh_items} 条），不用于周报统计")
        return []
    print(f"[weekly] 加载当日情报 {len(items)} 条（近 {max_content_age_days} 天 {len(fresh)} 条）"
          f"供周报使用")
    return items


def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    paths = config.get("paths", {})
    output_dir = Path(paths.get("output_dir", r"D:\osint\data"))
    vault = config.get("knowledge_base", {}).get("vault_path", r"D:\Codex输出\视频知识库")

    kb = load_knowledge(vault)
    kb.load_all()
    # persona 留空：周循环只做验证与汇总，不需要画像注入（裁判链路保持中立）
    analyzer = MacroAnalyzer(config, "", kb)
    engine = HypothesisEngine(config, kb, analyzer)

    intel_items = load_week_intel(output_dir)
    engine.run_weekly_cycle(intel_items=intel_items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
