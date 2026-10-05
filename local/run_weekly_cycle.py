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


def load_week_intel(output_dir, max_content_age_days=10, min_fresh_items=5):
    """加载**上一个完整周**的情报供周报统计。

    2026-10-05 修：原先只读**当日**文件（intel_今天.jsonl）。但周报在周一生成，
    统计的是"上周"（上周一~上周日），当日文件里只有零星几条落在该窗口（实测
    10-05 只匹配到 10-04 的 183 条，而上一完整周实际有 12102 条）→ AI 误判
    "数据严重不足，仅 7 条可用"。改为按上周窗口的日期，逐日读 intel_YYYYMMDD.jsonl。

    新鲜度仍用「窗口内条目数」判定（不用"最新一条距今几天"——会被源站错误
    时间戳的未来条目绕过，年龄算出负数）。窗口 = 上周一 00:00 ~ 上周日 23:59:59。
    """
    today_local = datetime.now()
    this_monday = (today_local - timedelta(days=today_local.weekday())
                   ).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = this_monday - timedelta(days=7)
    week_end = this_monday - timedelta(seconds=1)

    items = []
    seen_days = 0
    day = week_start
    while day <= week_end:
        f = Path(output_dir) / f"intel_{day.strftime('%Y%m%d')}.jsonl"
        if f.exists() and f.stat().st_size > 0:
            seen_days += 1
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        day += timedelta(days=1)

    lo = week_start.strftime("%Y-%m-%d")
    hi = week_end.strftime("%Y-%m-%d")
    fresh = [i for i in items if lo <= str(i.get("published_at") or "")[:10] <= hi]
    if len(fresh) < min_fresh_items:
        print(f"[weekly] 上周窗口 {lo}~{hi} 仅 {len(fresh)} 条情报"
              f"（下限 {min_fresh_items} 条），不用于周报统计")
        return []
    print(f"[weekly] 加载上周情报 {len(fresh)} 条（窗口 {lo}~{hi}，覆盖 {seen_days} 个日文件）"
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
