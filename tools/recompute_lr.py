# -*- coding: utf-8 -*-
r"""recompute_lr.py - 用存量 gate/conf 重算全矩阵 LR（2026-09-28）

**为什么需要**：`derive_lr()` 加入 gate 权重后（弱相关判定按比例降权），
存量 4181 行的 lr 仍是用旧公式算的。重算只需读存量 `code`/`conf`/`gate`
三字段——**无需重跑 AI**（这是当初落盘 conf/gate 的设计目的）。

用法：
    python tools/recompute_lr.py --dry      # 预览变化，不写盘
    python tools/recompute_lr.py            # 重算 + 写盘 + 重算后验
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只预览，不写盘")
    args = ap.parse_args()

    from ach_matrix import derive_lr, gate_weight

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    matrix_file = BASE / "hypotheses" / "ach_matrix.json"
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    m = json.loads(matrix_file.read_text(encoding="utf-8"))

    changed = same = no_gate = 0
    by_weight = {}
    for ev in m["evidence"]:
        for hid, d in (ev.get("diagnosis") or {}).items():
            if not isinstance(d, dict) or d.get("code") not in ("C", "I"):
                continue
            if d.get("conf") is None:
                continue
            g = d.get("gate")
            if g is None:
                no_gate += 1
                continue
            new_lr = derive_lr(d["code"], d["conf"], gate=g)
            old_lr = d.get("lr", 1.0)
            w = gate_weight(g)
            by_weight.setdefault(round(w, 2), [0, 0.0])
            by_weight[round(w, 2)][0] += 1
            by_weight[round(w, 2)][1] += abs(new_lr - old_lr)
            if abs(new_lr - old_lr) > 0.0005:
                changed += 1
            else:
                same += 1
            if not args.dry:
                d["lr"] = new_lr
                d["schema"] = "v3_gate_weighted"

    print(f"[RECOMPUTE] C/I 判定：变动 {changed} / 未变 {same} / 无 gate {no_gate}")
    print("\n按 gate 权重的降权分布:")
    for w in sorted(by_weight, reverse=True):
        n, tot = by_weight[w]
        print(f"  weight={w:.2f}: {n:4d} 条 | 平均 LR 变化 {tot / n:.4f}")

    if args.dry:
        print("\n[RECOMPUTE] --dry 模式，未写盘")
        return

    # 重算后验并写回
    from ach_matrix import ACHMatrix
    ach = ACHMatrix(matrix_file, majors)
    ach.data = m
    before = {k: v.get("posterior") for k, v in (m.get("scoring") or {}).items()}
    ach.bayesian_update(hyps)
    after = {k: v.get("posterior") for k, v in ach.data["scoring"].items()}
    try:
        ach.sensitivity_analysis()
    except Exception as ex:
        print(f"[RECOMPUTE] sensitivity failed: {str(ex)[:100]}")
    ach.save()
    try:
        ach.export_markdown(BASE)
    except Exception as ex:
        print(f"[RECOMPUTE] export failed: {str(ex)[:100]}")
    hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 后验变化 ===")
    print(f"{'假设':14s} {'重算前':>8s} {'重算后':>8s} {'变化':>8s}")
    for hid in after:
        b, a = before.get(hid), after[hid]
        if b is None:
            continue
        print(f"{hid:14s} {b:8.3f} {a:8.3f} {a - b:+8.3f}")
    print("\n[RECOMPUTE] 完成")


if __name__ == "__main__":
    main()
