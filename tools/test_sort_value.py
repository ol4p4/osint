# -*- coding: utf-8 -*-
r"""test_sort_value.py - 当前排序到底有没有用？（2026-09-30，只读）

**问题**：Pass 2 的 `_sel_key` 按 relevance 降序择优，cap=6。但实测各分数带的
真信号率几乎持平（10-13%），而低分带反而更高——**怀疑排序无区分力**。

**方法**：对每个 (假设, 日) 的候选池，比较三种选法的真信号产出：
  - 现状：按 relevance 降序取 top-6
  - 随机：随机取 6 条（多次平均）
  - 理想：按真实 gate 降序取 top-6（上限参考）

**注意**：evidence_log 是**筛选后**的产物，不同分数带可能来自不同入选路径
（pre-cap 时代全量入库 / 上卷 / 当前 cap），存在选择偏差。所以本测试同时报告
"池子大小"，池子远大于 6 说明该组数据确实经历过 cap 筛选。

用法：
    python tools/test_sort_value.py
"""
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"

GATE_SIGNAL = 0.15
CAP = 6


def ekey(d, s):
    return "ev_" + hashlib.sha256((str(d) + "|" + str(s)[:80]).encode("utf-8")).hexdigest()[:10]


def main():
    m = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))
    gate = {}
    for r in m["evidence"]:
        d = r.get("diagnosis") or {}
        gs = [float(v["gate"]) for v in d.values()
              if isinstance(v, dict) and v.get("gate") is not None]
        if gs:
            gate[r["key"]] = max(gs)

    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]

    # 构造 (假设, 日) 候选池：只含已诊断（有 gate 标签）的条目
    pools = defaultdict(list)
    for h in majors:
        for e in (h.get("evidence_log") or []):
            k = ekey(e.get("date"), e.get("summary"))
            if k not in gate:
                continue
            pools[(h["id"], str(e.get("date"))[:10])].append(
                (float(e.get("relevance") or 0), gate[k], bool(e.get("from_child"))))

    multi = {k: v for k, v in pools.items() if len(v) > CAP}
    print(f"(假设,日) 组合 {len(pools)} | 池子 > cap({CAP}) 的 {len(multi)} 个")
    if not multi:
        print("无池子超过 cap——所有证据都已入库，排序无实际影响")
        return

    tot_actual = tot_rand = tot_oracle = 0
    tot_pool_sig = 0
    print(f"\n{'假设':<8}{'日期':<12}{'池':>5}{'池中信号':>9}"
          f"{'现状top6':>10}{'随机top6':>10}{'理想top6':>10}")
    for (hid, day), cands in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:16]:
        pool_sig = sum(1 for _r, g, _f in cands if g >= GATE_SIGNAL)
        # 现状：relevance 降序
        by_rel = sorted(cands, key=lambda x: -x[0])[:CAP]
        a = sum(1 for _r, g, _f in by_rel if g >= GATE_SIGNAL)
        # 理想：gate 降序
        by_gate = sorted(cands, key=lambda x: -x[1])[:CAP]
        o = sum(1 for _r, g, _f in by_gate if g >= GATE_SIGNAL)
        # 随机基线：不真抽样，直接用解析期望（池中信号数 × k / 池大小），
        # 更精确且确定性（无 RNG 依赖）
        r = pool_sig * CAP / len(cands)
        tot_actual += a
        tot_rand += r
        tot_oracle += o
        tot_pool_sig += pool_sig
        print(f"{hid:<8}{day:<12}{len(cands):>5}{pool_sig:>9}"
              f"{a:>10}{r:>10.1f}{o:>10}")

    print("\n" + "=" * 66)
    n = len(multi)
    print(f"汇总（{n} 个超 cap 的池子）:")
    print(f"  池中真信号总计     {tot_pool_sig}")
    print(f"  现状（relevance）  {tot_actual}   ← 当前生产行为")
    print(f"  随机选            {tot_rand:.1f}")
    print(f"  理想（oracle）     {tot_oracle}")
    print()
    print(f"  现状 vs 随机: {tot_actual - tot_rand:+.1f} 条")
    print(f"  现状 vs 理想: {tot_actual - tot_oracle:+d} 条（差距 = 排序浪费掉的信号）")
    print()
    if tot_oracle > 0:
        print(f"  排序效率: 现状抓到理想的 {100*tot_actual/tot_oracle:.0f}%，"
              f"随机的 {100*tot_rand/tot_oracle:.0f}%")
    if tot_actual <= tot_rand * 1.1:
        print("  ⚠ 现状排序与随机无显著差异——relevance 排序**没有区分力**")
    else:
        print("  ✓ 现状排序优于随机，有实际作用")


if __name__ == "__main__":
    main()
