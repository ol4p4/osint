# -*- coding: utf-8 -*-
r"""backfill_weekly.py - 补录缺失的周报（2026-10-08）

## 背景

发现两处周报缺口：

| 缺口 | 原因 |
|---|---|
| `hypothesis_weekly_20260907` 缺失 | 2026-09-07 那周周循环**静默失败**（AGENTS.md 记录：`hypothesis_engine.py:419` 的局部 datetime import 导致 UnboundLocalError，周循环每次必死在验证到期假设之前）。同一事故也吞掉了当周政策追踪。 |
| `policy_week_202636` / `202637` 缺失 | 同上（政策追踪挂在周循环 Step3.5） |

## 为什么不在原位置补

`_save_ai_weekly_summary` 用**当天日期**命名文件（`datetime.now()`），直接补会把
08-31~09-06 的内容标成 2026-10-08，与真实周次错位。本脚本显式传 `week_offset`
回溯到目标周，并把产物写成 `_backfilled_` 后缀，**与常规周报区分开**——
补录的内容是"事后追述"，不该混进当期报告目录的时间序。

## 数据前提（已核实）

- 08-31~09-06 的 intel 文件齐全：7 个文件共 7930 条，69% 有中文、1204 条已研判
- **不重跑 ACH/假设链**：只调 `_save_ai_weekly_summary` 这一函数的周报生成部分，
  不碰假设树、不触发贝叶斯更新（补录只补文档，不改判定状态）

用法：
    python local/backfill_weekly.py --week-offset 4 --dry    # 先看窗口与数据量
    python local/backfill_weekly.py --week-offset 4          # 实跑（调 AI 生成）
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402


def week_window(week_offset, today=None):
    """返回 (week_start, week_end, week_start_str, week_end_str)。

    口径与 hypothesis_engine._save_ai_weekly_summary 完全一致：
    offset=0 是上一个完整周（上周一 00:00 ~ 上周日 23:59:59）。
    """
    today_local = today or datetime.now()
    this_monday = (today_local - timedelta(days=today_local.weekday())
                   ).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = this_monday - timedelta(seconds=1)
    week_start = this_monday - timedelta(days=7)
    if week_offset:
        week_start -= timedelta(days=7 * week_offset)
        week_end -= timedelta(days=7 * week_offset)
    return week_start, week_end, week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")


def load_window_intel(output_dir, start_str, end_str):
    """按窗口日期逐日读 intel_YYYYMMDD.jsonl（对齐 run_weekly_cycle.load_week_intel）。"""
    items = []
    d = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    while d <= end:
        p = Path(output_dir) / ("intel_" + d.strftime("%Y%m%d") + ".jsonl")
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except Exception:
                        pass
            except Exception as ex:
                print("  [warn] 读取失败 %s: %s" % (p.name, str(ex)[:60]))
        d += timedelta(days=1)
    return items


def main():
    ap = argparse.ArgumentParser(description="补录缺失的周报")
    ap.add_argument("--week-offset", type=int, required=True,
                    help="回溯周数：0=上一个完整周，1=再往前一周，以此类推")
    ap.add_argument("--dry", action="store_true", help="只显示窗口与数据量，不调 AI")
    ap.add_argument("--out", default=None, help="输出文件路径（默认自动命名）")
    args = ap.parse_args()

    ws, we, ws_s, we_s = week_window(args.week_offset)
    print("[BACKFILL] 目标窗口: %s ~ %s (week_offset=%d)" % (ws_s, we_s, args.week_offset))

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    out_dir = Path(cfg.get("paths", {}).get("output_dir", r"D:\osint\data"))
    hyps_file = out_dir / "hypotheses" / "active_hypotheses.json"
    ach_file = out_dir / "hypotheses" / "ach_matrix.json"

    intel = load_window_intel(out_dir, ws_s, we_s)
    print("[BACKFILL] 窗口内情报: %d 条" % len(intel))
    if not intel:
        print("[BACKFILL] 窗口内无数据，中止（不生成空周报）")
        return 1
    if args.dry:
        # 抽样看看主题分布
        from collections import Counter
        c = Counter()
        for it in intel:
            t = (it.get("cn_title") or it.get("title") or "")[:40]
            c[t[:16]] += 1
        print("[BACKFILL] dry-run，最高频标题前缀:")
        for k, v in c.most_common(5):
            print("    %-18s %d" % (k, v))
        return 0

    hyps = json.loads(hyps_file.read_text(encoding="utf-8"))
    ach = None
    if ach_file.exists():
        try:
            ach = json.loads(ach_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    print("[BACKFILL] 假设树 %d 节点，ACH %s" % (len(hyps), "有" if ach else "无"))

    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    from hypothesis_engine import HypothesisEngine

    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    # 签名是 (config, kb, analyzer)；output_dir 从 config.paths 读，不接受构造参数
    engine = HypothesisEngine(cfg, kb, MacroAnalyzer(cfg, "", kb))

    print("[BACKFILL] 调用周报生成（只调 _save_ai_weekly_summary，不动假设树/不跑 ACH）…")
    engine._save_ai_weekly_summary(hyps, ach_matrix=ach, intel_items=intel,
                                   week_offset=args.week_offset)

    # 产物文件名：_save_ai_weekly_summary 产出的是 weekly_<当天>.md
    # （不是 hypothesis_weekly_*，后者由 run_weekly_cycle 的假设列表段单独产出——
    #  踩坑：首版检测 hypothesis_weekly_ 前缀，明明生成成功却报"未找到产物"）
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    produced = out_dir / "reports" / ("weekly_" + today_str + ".md")
    target = Path(args.out) if args.out else (
        out_dir / "reports" / ("weekly_backfilled_%s_to_%s.md" % (
            ws_s.replace("-", ""), we_s.replace("-", ""))))
    if produced.exists():
        target.write_text(produced.read_text(encoding="utf-8"), encoding="utf-8")
        # 常规文件若被本期覆盖会误导，删掉（补录不该占用当期文件名）
        try:
            produced.unlink()
            print("[BACKFILL] 已移除当期名字的临时产物: %s" % produced.name)
        except OSError:
            pass
        print("[BACKFILL] 补录周报 -> %s" % target)
    else:
        print("[BACKFILL] 未找到产物 %s（AI 生成可能整体失败）" % produced.name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
