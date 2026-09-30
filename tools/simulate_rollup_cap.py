# -*- coding: utf-8 -*-
r"""simulate_rollup_cap.py - 上卷 cap 取值模拟（2026-09-30，只读）

**背景**：Pass 3 证据上卷**不受 cap 约束**——实测 9-29 单日给 HM101 灌 481 条
（350 条来自 HM101_A_s2），JEV 日消耗从 5 万 token 暴涨到 798 万（100 倍）。
用户额度 $5 一个月过期，按此速度 $4.35 只够 13 天。

**关键前提**：上卷证据的信号率（11.8%）与直接证据（13.1%）**几乎持平**——
上卷本身有效，问题纯粹是**量**。所以修法是加上限，不是关掉。

**本工具**回答：上卷 cap 取多少，能在"砍掉冗余量"与"保住真信号"之间平衡？
对每个假设，按 `_sel_key` 排序（TF-IDF 优先 → relevance 降序）后
按日取 top-N，统计真信号保留率。

**只读，不写盘。**

用法：
    python tools/simulate_rollup_cap.py
"""
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"

GATE_SIGNAL = 0.15   # 真信号门槛（与 jev_client GATE_ABS_THRESHOLD 一致）


def evidence_key(ev):
    raw = str(ev.get("date", "")) + "|" + str(ev.get("summary", ""))[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def main():
    m = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))
    gate_of = {}
    for r in m["evidence"]:
        d = r.get("diagnosis") or {}
        gs = [float(v.get("gate")) for v in d.values()
              if isinstance(v, dict) and v.get("gate") is not None]
        if gs:
            gate_of[r["key"]] = max(gs)

    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]

    print("=" * 72)
    print("上卷证据质量与 cap 代价模拟")
    print("=" * 72)

    total_rolled = 0
    total_diag = 0
    total_sig = 0
    per_hyp = {}

    for h in majors:
        rolled = [e for e in (h.get("evidence_log") or []) if e.get("from_child")]
        diag = [e for e in rolled if evidence_key(e) in gate_of]
        sig = [e for e in diag if gate_of[evidence_key(e)] >= GATE_SIGNAL]
        per_hyp[h["id"]] = (len(rolled), len(diag), len(sig), diag)
        total_rolled += len(rolled)
        total_diag += len(diag)
        total_sig += len(sig)
        rate = 100 * len(sig) / max(len(diag), 1)
        print(f"  {h['id']}: 上卷 {len(rolled)} 条 | 已诊断 {len(diag)} | "
              f"真信号 {len(sig)} ({rate:.1f}%)")

    print(f"\n  合计: 上卷 {total_rolled} 条，已诊断 {total_diag} 条，"
          f"真信号 {total_sig} 条 ({100*total_sig/max(total_diag,1):.1f}%)")

    # ---- cap 模拟：按日取 top-N ----
    print("\n" + "=" * 72)
    print("cap 模拟（每假设每日上卷取 top-N，按 relevance 降序）")
    print("=" * 72)
    print(f"{'cap':>5} {'保留条数':>9} {'保留率':>8} {'真信号保留':>11} "
          f"{'真信号率':>9} {'相对基线':>10}")

    base_rate = total_sig / max(total_diag, 1)

    for cap in [1, 2, 3, 4, 6, 8, 10, 999]:
        kept_diag = 0
        kept_sig = 0
        for hid, (_rt, _dt, _st, diag) in per_hyp.items():
            by_day = defaultdict(list)
            for e in diag:
                by_day[str(e.get("date"))[:10]].append(e)
            for day, evs in by_day.items():
                # 排序：TF-IDF 优先（relevance 语义不同），这里统一按 relevance 降序
                evs.sort(key=lambda x: -float(x.get("relevance") or 0))
                for e in evs[:cap]:
                    kept_diag += 1
                    if gate_of[evidence_key(e)] >= GATE_SIGNAL:
                        kept_sig += 1
        rate = kept_sig / max(kept_diag, 1)
        print(f"{cap:>5} {kept_diag:>9} {100*kept_diag/max(total_diag,1):>7.1f}% "
              f"{kept_sig:>11} {100*rate:>8.1f}% {rate/base_rate:>9.2f}x")

    print(f"\n  基线（全保留）真信号率: {100*base_rate:.1f}%")

    # ---- 每日量估算（cap 生效后的稳态）----
    print("\n" + "=" * 72)
    print("稳态日产量估算（cap 生效后）")
    print("=" * 72)
    print(f"{'配置':<34} {'条/天':>8} {'token/天':>12} {'$/天':>9} {'$4.35 可用':>12}")
    for cap in [2, 4, 6, 8]:
        n = len(majors) * (6 + cap)   # 直接 6 + 上卷 cap
        tok = n * 1500                # 每条证据 gate+choice 两次调用约 1500 token
        cost = tok * 0.042 / 1e6
        days = 4.35 / cost if cost > 0 else 999
        print(f"直接6 + 上卷{cap}（{len(majors)}假设）{'':<12} {n:>8} {tok:>12,} "
              f"${cost:>8.4f} {days:>10.0f} 天")

    # 现状（无 cap）对比
    cur_daily = 1355  # 实测 9-29 单日
    tok = cur_daily * 1500
    cost = tok * 0.042 / 1e6
    print(f"\n现状（无上卷 cap，实测 9-29）{'':<12} {cur_daily:>8} {tok:>12,} "
          f"${cost:>8.4f} {4.35/cost:>10.0f} 天")


if __name__ == "__main__":
    main()
