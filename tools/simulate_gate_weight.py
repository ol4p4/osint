# -*- coding: utf-8 -*-
r"""simulate_gate_weight.py - 门控降权阈值的模拟对比（2026-09-30，只读不写盘）

**问题**：186 条 C/I 判定里 60% 来自 gate < 0.3 的弱相关证据。
`gate_weight()` 在 gate < 0.20 时把 LR 强度衰减到 0.4 倍，但实测这个降权不够狠——
`HM102`/`HM100` 被大量弱 I 判定累积压到地板 0.05。

**本工具**：对多个 `GATE_WEAK_THRESHOLD` 候选值，用存量 gate/conf 重算后验，
输出对比表。**不写盘**——供人工判断哪个阈值合理。

用法：
    python tools/simulate_gate_weight.py
"""
import json
import math
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))

from ach_matrix import (derive_lr, POSTERIOR_CAP, POSTERIOR_FLOOR,
                        LR_C_STRENGTH, LR_I_STRENGTH)

# 候选阈值：当前 0.20 / 0.35 / 0.50
CANDIDATES = [0.20, 0.35, 0.50]


def posterior_with_threshold(m, majors, floor):
    """按给定 gate_weight 阈值重算各 major 后验（纯函数，不改状态）。"""
    out = {}
    for h in majors:
        hid = h["id"]
        prior = float(h.get("base_confidence") or 0.5)
        odds = prior / max(1 - prior, 0.01)
        support = refute = 0
        for ev in m["evidence"]:
            d = (ev.get("diagnosis") or {}).get(hid)
            if not d or d.get("code") not in ("C", "I"):
                continue
            conf = d.get("conf")
            gate = d.get("gate")
            if conf is None:
                lr = float(d.get("lr") or 1.0)   # 旧格式，不参与重算
            else:
                # 内联 gate_weight 逻辑（可调阈值）
                try:
                    g = float(gate) if gate is not None else None
                except (TypeError, ValueError):
                    g = None
                if g is None:
                    w = 1.0
                elif g >= floor:
                    w = 1.0
                else:
                    w = max(0.4, g / floor)
                c = max(0.0, min(1.0, float(conf)))
                if d["code"] == "C":
                    lr = 1.0 + c * (LR_C_STRENGTH - 1.0) * w
                else:
                    lr = 1.0 - c * (1.0 - LR_I_STRENGTH) * w
            odds *= lr
            if d["code"] == "C":
                support += 1
            else:
                refute += 1
        post = odds / (1 + odds)
        out[hid] = {
            "posterior": round(max(min(post, POSTERIOR_CAP), POSTERIOR_FLOOR), 3),
            "support": support, "refute": refute,
            "prior": round(prior, 3),
        }
    return out


def main():
    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    matrix_file = BASE / "hypotheses" / "ach_matrix.json"
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    m = json.loads(matrix_file.read_text(encoding="utf-8"))

    # gate 分布概览
    gates = []
    for ev in m["evidence"]:
        for d in (ev.get("diagnosis") or {}).values():
            if d.get("code") in ("C", "I") and d.get("gate") is not None:
                gates.append(float(d["gate"]))
    gates.sort()
    print(f"C/I 判定 {len(gates)} 条，gate 分布：")
    for p in (10, 25, 50, 75, 90):
        print(f"  p{p}: {gates[int(len(gates) * p / 100)]:.2f}")
    print(f"  max: {gates[-1]:.2f}")
    n_weak = sum(1 for g in gates if g < 0.3)
    print(f"  gate<0.3（弱相关）: {n_weak} 条 ({100 * n_weak / len(gates):.0f}%)")

    results = {}
    for thr in CANDIDATES:
        results[thr] = posterior_with_threshold(m, majors, thr)

    print("\n" + "=" * 74)
    print("后验对比（不写盘，仅供判断）")
    print("=" * 74)
    titles = {h["id"]: h.get("title", "")[:20] for h in majors}
    header = f"{'假设':8s} {'先验':>6s} " + " ".join(f"{'thr=' + str(t):>9s}" for t in CANDIDATES) + "  标题"
    print(header)
    for hid in results[CANDIDATES[0]]:
        row = [results[t][hid] for t in CANDIDATES]
        cells = " ".join(f"{r['posterior']:9.3f}" for r in row)
        print(f"{hid:8s} {row[0]['prior']:6.2f} {cells}  {titles.get(hid, '')}")

    print("\n各阈值下 C/I 有效数与后验分布：")
    for thr in CANDIDATES:
        rr = results[thr]
        at_floor = sum(1 for r in rr.values() if r["posterior"] <= 0.051)
        at_cap = sum(1 for r in rr.values() if r["posterior"] >= 0.949)
        print(f"  thr={thr}: 贴地板 {at_floor} 个 / 贴上限 {at_cap} 个 / 共 {len(rr)}")

    # 变化幅度
    print("\n相对当前（thr=0.20）的后验变化：")
    base = results[CANDIDATES[0]]
    for thr in CANDIDATES[1:]:
        print(f"  thr={thr}:")
        for hid, r in results[thr].items():
            d = r["posterior"] - base[hid]["posterior"]
            if abs(d) >= 0.01:
                print(f"    {hid:8s} {base[hid]['posterior']:.3f} -> {r['posterior']:.3f} ({d:+.3f})")


if __name__ == "__main__":
    main()
