# -*- coding: utf-8 -*-
r"""merge_ci_probe.py - 收编云端探针读数（2026-10-14 新增）

## 为什么需要它

`tools/probe_mega.py` 的探针读数依赖 JEV Noul。**本地 JEV 免费通道被地域封锁**
（HTTP 451，实测 2026-10-08 起），probe 本地连续多日跑不动（probe_readings
停在 10-08）；而 CI（US runner）上 JEV 完全可用（同日实测「扫描 100 失败 0」）。
本地做不了、云端能做——但 `data/hypotheses/` 的唯一有效写入方是本地
（CI 改它会被 checkout 丢弃），故 CI 侧只写独立产物 `probe_cloud_YYYYMMDD.json`，
由本脚本在本地收编。

## 它做什么

1. 读近 N 天的 `probe_cloud_*.json`（CI 产物，随 git 带回）
2. 与 probe_readings.json 已有的按 `at` 时间戳去重（幂等）
3. 追加进 probe_readings.json（保留历史序列的连续性）
4. 把最新读数写入对应探针节点的 `probe_reading` 字段（**不写 confidence**——
   探针概率是用户的问题，工具只提供客观输入，这是原设计）

## 与本地 probe 的关系

本地 probe 若恢复可用（JEV 解封），两条路径都写 probe_readings.json，
靠 `at` 去重保证不重复。CI 产物标 `source: "ci"` 便于区分来源。

## 用法

    python tools/merge_ci_probe.py --dry     # 只看候选
    python tools/merge_ci_probe.py           # 真合并
    python tools/merge_ci_probe.py --days 3  # 只收近 3 天产物（默认 7）

由 refresh.py 的 run_hypothesis_chain 调用（merge-ci 之后），critical=False：
产物来自外部（CI），缺失/损坏不该阻塞核心链节流戳。
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"
READINGS_FILE = BASE / "hypotheses" / "probe_readings.json"


def _product_files(directory, days):
    """近 N 天的 probe_cloud_*.json（按文件名日期倒序，新的先）。"""
    files = []
    for p in Path(directory).glob("probe_cloud_*.json"):
        stem = p.stem
        parts = stem.split("_")
        d = next((x for x in reversed(parts) if len(x) == 8 and x.isdigit()), None)
        if d:
            files.append((d, p))
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        files = [(d, p) for d, p in files if d >= cutoff]
    files.sort(key=lambda x: x[0], reverse=True)
    return files


def main():
    ap = argparse.ArgumentParser(description="收编云端探针读数")
    ap.add_argument("--dir", default=str(PROJECT), help="CI 产物所在目录")
    ap.add_argument("--days", type=int, default=7, help="只收近 N 天产物（0=不限）")
    ap.add_argument("--dry", action="store_true", help="只看候选，不写盘")
    args = ap.parse_args()

    products = _product_files(args.dir, args.days)
    if not products:
        print("[MERGE-PROBE] 无云端探针产物，跳过")
        return
    print("[MERGE-PROBE] 产物 %d 个: %s"
          % (len(products), ", ".join(p.name for _, p in products[:5])))

    # 已有读数的时间戳集合（幂等判据）
    history = []
    if READINGS_FILE.exists():
        try:
            history = json.loads(READINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    seen_at = {str(h.get("at") or "") for h in history if isinstance(h, dict)}

    # 逐产物读，按 at 去重（同一产物重复拉回不重复收）
    added = []
    for _d, path in products:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception as ex:
            print("[MERGE-PROBE] 读取失败 %s: %s" % (path.name, str(ex)[:80]))
            continue
        if not isinstance(snap, dict) or not snap.get("stats"):
            continue
        at = str(snap.get("at") or "")
        if not at or at in seen_at:
            continue
        seen_at.add(at)
        snap["source"] = "ci"    # 标来源便于区分（本地 probe 无此字段）
        added.append(snap)

    if not added:
        print("[MERGE-PROBE] 无新读数（幂等），退出")
        return

    # 时间序追加（旧→新），保持序列语义
    added.sort(key=lambda s: str(s.get("at") or ""))
    print("[MERGE-PROBE] 新读数 %d 条（最近: %s）" % (len(added), added[-1].get("at")))

    latest = added[-1]
    stats = latest.get("stats") or {}
    for pid, s in stats.items():
        print("[MERGE-PROBE]     %s: 信号率 %.2f%%（%d/%d 条）"
              % (pid, float(s.get("signal_ratio") or 0) * 100,
                 s.get("signal_count", 0), s.get("samples", 0)))

    if args.dry:
        print("[MERGE-PROBE] --dry 结束，未写盘")
        return

    if not HYP_FILE.exists():
        print("[MERGE-PROBE] 假设树不存在，跳过")
        return

    # ① 追加读历史
    history.extend(added)
    READINGS_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[MERGE-PROBE] probe_readings.json 累计 %d 次" % len(history))

    # ② 最新读数写节点字段（不写 confidence，原设计）
    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    now_s = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    written = 0
    for n in nodes:
        if n.get("id") in stats:
            n["probe_reading"] = dict(stats[n["id"]], measured_at=now_s,
                                      measured_source="ci")
            written += 1
    if written:
        HYP_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print("[MERGE-PROBE] 已写入 %d 个探针节点的 probe_reading（confidence 未改动）" % written)


if __name__ == "__main__":
    main()
