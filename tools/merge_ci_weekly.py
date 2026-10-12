# -*- coding: utf-8 -*-
r"""merge_ci_weekly.py - 本地收编云端周报（2026-10-12 新增）

## 背景

周报上云后（`cloud/weekly_report_cloud.py`），CI 在周一生成
`weekly_cloud_YYYYMMDD.md` 并随仓库带回本地。本工具把它收进本地标准位置
`data/reports/`，供人阅读与下游消费。

## 为什么需要"收编"这一步（而不是让云端直接写 reports/）

CI 侧只写仓库根自己的产物——`data/reports/` 不在 git 里（`data/*` 被 ignore），
写进去会被 checkout 丢弃（同 `data/hypotheses/` 的教训，见 AGENTS.md）。

## 三种情形的处置（这是本工具的核心逻辑）

| 本地已有的 weekly_<date>.md | 云端产物 | 动作 |
|---|---|---|
| 不存在 | 有 | **收编**：写成本地命名，标注来源 |
| 存在但是失败占位（含"AI 周报生成失败"） | 有 | **替换**：本地 AI 挂了、云端成了，正是上云要解决的场景 |
| 存在且是真实内容 | 有 | **保留本地**，只记一行提示（本地能跑通时优先本地——它带本地证据链视角） |

**为什么不无脑覆盖**：本地周报由周循环在完整上下文里生成（含当周 ACH 增量诊断
与验证结果），云端只读快照。本地成功时它就是更完整的版本；云端是**兜底**，
不是竞品。反过来，本地失败时（本轮实测：周循环 AI 断供挂死、周报未产出）
云端产物是唯一可用的。

## 用法

    python tools/merge_ci_weekly.py --dry     # 只看会做什么
    python tools/merge_ci_weekly.py           # 实际收编
    python tools/merge_ci_weekly.py --base <data目录>   # 测试用
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\osint")
DEFAULT_REPO = PROJECT
DEFAULT_BASE = PROJECT / "data"

# 周报里 AI 总结段的失败标记（本地 hypothesis_engine 生成失败时写入）。
# 命中即认为"这份周报没有真实 AI 内容"，允许被云端版本替换。
FAIL_MARKERS = ("AI 周报生成失败",)
# 收编时标注来源，便于事后判断"这份是谁生成的"。措辞只陈述**生成路径**
# （云端脚本），不写死"CI"——本地排障时也会跑同一个脚本产出同格式产物。
ADOPT_BANNER = "> 来源：云端生成路径（cloud/weekly_report_cloud.py）；本地周循环本轮未产出或失败\n"


def _product_date(path):
    stem = Path(path).stem
    for p in reversed(stem.split("_")):
        if len(p) == 8 and p.isdigit():
            return p
    return None


def is_failed_report(text):
    """判断一份周报是否为"无真实 AI 内容"的失败产物"""
    head = (text or "")[:4000]
    return any(m in head for m in FAIL_MARKERS)


def main():
    ap = argparse.ArgumentParser(description="把 CI 侧周报收编进 data/reports/")
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="仓库根（CI 产物所在）")
    ap.add_argument("--base", default=str(DEFAULT_BASE), help="data 目录")
    ap.add_argument("--days", type=int, default=21, help="只看近 N 天的云端产物（0=不限）")
    ap.add_argument("--dry", action="store_true", help="只看会做什么，不写盘")
    args = ap.parse_args()

    repo = Path(args.repo)
    base = Path(args.base)
    reports = base / "reports"

    products = sorted(repo.glob("weekly_cloud_*.md"),
                      key=lambda p: _product_date(p) or "")
    if not products:
        print("[MERGE-WEEKLY] 无云端周报产物，跳过")
        return

    cutoff = None
    if args.days > 0:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y%m%d")

    adopted = kept = 0
    for f in products:
        d = _product_date(f)
        if cutoff and d and d < cutoff:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as ex:
            print("[MERGE-WEEKLY] 读取失败 %s: %s" % (f.name, str(ex)[:80]))
            continue
        if len(text) < 500:
            print("[MERGE-WEEKLY] 跳过 %s（内容过短 %d 字节）" % (f.name, len(text)))
            continue

        target = reports / ("weekly_" + d + ".md") if d else None
        if target is None:
            print("[MERGE-WEEKLY] 跳过 %s（文件名无日期）" % f.name)
            continue

        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8")
            except Exception:
                existing = ""
            if not is_failed_report(existing):
                kept += 1
                print("[MERGE-WEEKLY] 保留本地 %s（%d 字节，本地成功优先；云端版本见 %s）"
                      % (target.name, len(existing), f.name))
                continue
            print("[MERGE-WEEKLY] 本地 %s 是失败占位 → 用云端版本替换" % target.name)

        if args.dry:
            print("[MERGE-WEEKLY] [dry] 将收编 %s -> %s" % (f.name, target.name))
            adopted += 1
            continue

        reports.mkdir(parents=True, exist_ok=True)
        # 已有文件（失败占位）先备份，便于回溯
        if target.exists():
            try:
                shutil.copy2(str(target), str(target) + ".bak_pre_cloud")
            except Exception:
                pass
        body = ADOPT_BANNER + text if not text.startswith("#") else \
            text.replace("\n\n", "\n\n" + ADOPT_BANNER, 1)
        target.write_text(body, encoding="utf-8")
        adopted += 1
        print("[MERGE-WEEKLY] 已收编 %s -> %s（%d 字节）" % (f.name, target.name, len(body)))

    print("[MERGE-WEEKLY] 完成：收编 %d 份，保留本地 %d 份" % (adopted, kept))


if __name__ == "__main__":
    main()
