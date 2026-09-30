# -*- coding: utf-8 -*-
r"""backtest_ranking.py - 新旧排序键回测（2026-09-30，只读）

**问题**：`link_intel_hyp` 的 Pass 2 排序键已从 `(tfidf优先, -relevance, -base_score)`
改为 `(tfidf优先, -(kw_hits*10 + body_len/100))`。改动依据是 AUC 对比
（0.4762 → 0.6458），但 AUC 是**逐条**指标，需要验证**池级**效果：
在真实候选池上按新键取 top-6，能比旧键多抓多少真信号？

**方法**：用 cap 引入前（2026-09-12 之前）的完整候选池做回测——那时的
evidence_log 是全量入库，池子未被 cap 裁剪，可公平比较两种排序。

**只读，不写盘。**

用法：
    python tools/backtest_ranking.py
"""
import glob
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"

GATE_SIGNAL = 0.15
CAP = 6
# cap 引入日期（2026-09-12）；之前的池子完整
CUTOFF = "2026-09-12"


def ekey(d, s):
    return "ev_" + hashlib.sha256((str(d) + "|" + str(s)[:80]).encode("utf-8")).hexdigest()[:10]


def load_intel():
    idx = {}
    for f in sorted(glob.glob(str(BASE / "intel_2026*.jsonl")))[-40:]:
        if "raw" in Path(f).name or "final" in Path(f).name:
            continue
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("id"):
                idx[d["id"]] = d
    return idx


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
    intel_idx = load_intel()

    # 构造 (假设, 日) 候选池（只取 CUTOFF 之前 = 完整池）
    pools = defaultdict(list)
    for h in majors:
        for e in (h.get("evidence_log") or []):
            day = str(e.get("date"))[:10]
            if day >= CUTOFF:
                continue
            k = ekey(day, e.get("summary"))
            if k not in gate:
                continue
            iid = (e.get("intel_ids") or [None])[0]
            it = intel_idx.get(iid) if iid else None
            kw = len(it.get("keywords_hit") or []) if it else 0
            blen = len(str((it.get("cn_summary") or it.get("content_preview")
                            or it.get("content") or "") if it else ""))
            pools[(h["id"], day)].append({
                "gate": gate[k],
                "relevance": float(e.get("relevance") or 0),
                "kw": kw,
                "blen": blen,
                "tfidf": bool(e.get("ach_eligible")) and float(e.get("relevance") or 0) < 0.99,
            })

    multi = {k: v for k, v in pools.items() if len(v) > CAP}
    print(f"完整池（{CUTOFF} 前）: {len(pools)} 个 (假设,日) 组合 | 超 cap 的 {len(multi)} 个")
    if not multi:
        print("无可用回测池")
        return

    tot_pool_sig = tot_old = tot_new = tot_oracle = 0
    print(f"\n{'假设':<8}{'日期':<12}{'池':>5}{'信号':>5}"
          f"{'旧键top6':>10}{'新键top6':>10}{'理想':>6}")
    for (hid, day), cands in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:18]:
        psig = sum(1 for c in cands if c["gate"] >= GATE_SIGNAL)
        # 旧键：tfidf 优先 → relevance 降序
        old = sorted(cands, key=lambda c: (0 if c["tfidf"] else 1, -c["relevance"]))[:CAP]
        o = sum(1 for c in old if c["gate"] >= GATE_SIGNAL)
        # 新键：tfidf 优先 → kw*10 + blen/100 降序
        new = sorted(cands, key=lambda c: (0 if c["tfidf"] else 1,
                                           -(c["kw"] * 10 + c["blen"] / 100.0)))[:CAP]
        n = sum(1 for c in new if c["gate"] >= GATE_SIGNAL)
        orc = sorted(cands, key=lambda c: -c["gate"])[:CAP]
        r = sum(1 for c in orc if c["gate"] >= GATE_SIGNAL)
        tot_pool_sig += psig
        tot_old += o
        tot_new += n
        tot_oracle += r
        print(f"{hid:<8}{day:<12}{len(cands):>5}{psig:>5}{o:>10}{n:>10}{r:>6}")

    print("\n" + "=" * 66)
    print(f"汇总（{len(multi)} 个超 cap 池）:")
    print(f"  池中真信号总计    {tot_pool_sig}")
    print(f"  旧键（relevance） {tot_old}")
    print(f"  新键（kw+body）   {tot_new}")
    print(f"  理想（oracle）    {tot_oracle}")
    print()
    if tot_old > 0:
        print(f"  新键相对旧键: {tot_new - tot_old:+d} 条 "
              f"({100*(tot_new-tot_old)/tot_old:+.0f}%)")
    else:
        print(f"  新键相对旧键: {tot_new - tot_old:+d} 条（旧键一条都没抓到）")
    if tot_oracle > 0:
        print(f"  排序效率: 旧 {100*tot_old/tot_oracle:.0f}% → "
              f"新 {100*tot_new/tot_oracle:.0f}%（理想=100%）")


if __name__ == "__main__":
    main()
