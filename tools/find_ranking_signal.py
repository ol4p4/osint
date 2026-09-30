# -*- coding: utf-8 -*-
r"""find_ranking_signal.py - 寻找真正能预测 gate 的排序信号（2026-09-30，只读）

**问题**：Pass 2 的 `_sel_key` 按 relevance 降序择优，但实测 relevance 严重饱和
（HM102 9-10 池里 **86% 并列 1.0**）→ 排序退化为随机抽签。
`tools/test_sort_value.py` 实测：现状抓到理想值的 **8%**，比随机（12%）还差。

**本工具**：把 evidence_log 里的每条证据 join 回原始情报，测各个可用字段
对 gate（真信号标签）的预测力（AUC），找出**真正有区分力的排序键**。

候选信号：
  - relevance（现状）
  - intel 的 base_score / final_score（情报自身的关键词密度与综合分）
  - summary 长度、body 长度
  - 命中域数量
  - intel 的 story_size（事件被多少家媒体报道）

用法：
    python tools/find_ranking_signal.py
"""
import bisect
import glob
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"

GATE_SIGNAL = 0.15
GATE_NOISE = 0.08


def ekey(d, s):
    return "ev_" + hashlib.sha256((str(d) + "|" + str(s)[:80]).encode("utf-8")).hexdigest()[:10]


def load_intel():
    """intel_id -> 原始情报条目"""
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


def auc(pos, neg):
    """P(正样本分数 > 负样本分数)，秩和法（无随机数）"""
    if not pos or not neg:
        return float("nan")
    ns = sorted(neg)
    r = 0.0
    for s in pos:
        lo = bisect.bisect_left(ns, s)
        hi = bisect.bisect_right(ns, s)
        r += lo + 0.5 * (hi - lo)
    return r / (len(pos) * len(neg))


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
    print(f"情报索引 {len(intel_idx)} 条 | 已诊断证据 {len(gate)} 条")

    # 收集 (信号值, gate) 对
    samples = []      # (dict of signals, gate)
    join_fail = 0
    for h in majors:
        for e in (h.get("evidence_log") or []):
            k = ekey(e.get("date"), e.get("summary"))
            g = gate.get(k)
            if g is None:
                continue
            iid = (e.get("intel_ids") or [None])[0]
            it = intel_idx.get(iid) if iid else None
            if it is None:
                join_fail += 1
                continue
            sig = {
                "relevance": float(e.get("relevance") or 0),
                "n_domains": len(e.get("domains") or []),
                "summary_len": len(str(e.get("summary") or "")),
                "body_len": len(str(e.get("body") or "")),
                "base_score": float(it.get("base_score") or 0),
                "final_score": float(it.get("final_score") or 0),
                "story_size": float(it.get("story_size") or 0),
                "kw_hits": float(len(it.get("keywords_hit") or [])),
                "from_child": 1.0 if e.get("from_child") else 0.0,
            }
            samples.append((sig, g))
    print(f"成功 join {len(samples)} 条 | join 失败 {join_fail} 条")
    if not samples:
        print("无样本")
        return

    print("\n" + "=" * 66)
    print("各信号对 gate 的预测力（AUC，0.5=随机，>0.6 才算有区分力）")
    print("=" * 66)
    names = list(samples[0][0].keys())
    results = {}
    for name in names:
        vals = [s[name] for s, _g in samples]
        if max(vals) == min(vals):
            print(f"  {name:<14} 恒定值 {vals[0]}（无区分力）")
            continue
        pos = [s[name] for s, g in samples if g >= GATE_SIGNAL]
        neg = [s[name] for s, g in samples if g < GATE_NOISE]
        a = auc(pos, neg)
        results[name] = a
        # 并列率（饱和程度）
        from collections import Counter
        c = Counter(round(v, 4) for v in vals)
        top_tie = c.most_common(1)[0]
        tie_pct = 100 * top_tie[1] / len(vals)
        print(f"  {name:<14} AUC={a:.4f}  最大并列 {tie_pct:.1f}% (值={top_tie[0]})")

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    ranked = sorted(((a, n) for n, a in results.items() if a == a), reverse=True)
    for a, n in ranked:
        verdict = "★有区分力" if a >= 0.60 else ("弱" if a >= 0.55 else "≈随机")
        print(f"  {n:<14} AUC={a:.4f}  {verdict}")
    if ranked:
        best = ranked[0]
        print(f"\n最佳单信号: {best[1]} (AUC={best[0]:.4f})")
        cur = results.get("relevance", float("nan"))
        print(f"现状用 relevance (AUC={cur:.4f})")

    # ---- 组合信号：kw_hits 并列多，配 body_len / base_score 打破并列 ----
    print("\n" + "=" * 66)
    print("组合信号（并列多时需次级键）")
    print("=" * 66)

    def combo_auc(keyfn, label):
        pos = [keyfn(s) for s, g in samples if g >= GATE_SIGNAL]
        neg = [keyfn(s) for s, g in samples if g < GATE_NOISE]
        a = auc(pos, neg)
        vals = [keyfn(s) for s, _g in samples]
        from collections import Counter
        c = Counter(round(v, 6) for v in vals)
        tie = 100 * c.most_common(1)[0][1] / len(vals)
        print(f"  {label:<34} AUC={a:.4f}  最大并列 {tie:.1f}%")
        return a

    # 主键 kw_hits，次键 body_len（两者量纲差异大，用加权和）
    combo_auc(lambda s: s["kw_hits"] * 10 + s["body_len"] / 100.0,
              "kw_hits*10 + body_len/100")
    combo_auc(lambda s: s["kw_hits"] * 10 + s["base_score"],
              "kw_hits*10 + base_score")
    combo_auc(lambda s: s["kw_hits"] * 10 + s["base_score"] + s["body_len"] / 100.0,
              "kw_hits*10 + base_score + body_len/100")
    combo_auc(lambda s: s["body_len"],
              "body_len（单独）")
    combo_auc(lambda s: s["base_score"] * 10 + s["body_len"] / 100.0,
              "base_score*10 + body_len/100")
    # 现状对照
    combo_auc(lambda s: s["relevance"], "relevance（现状）")

    print("\n提示：kw_hits 有 46.5% 并列在 0（多数情报未记 keywords_hit），")
    print("      单独用仍需次级键；组合后并列率显著下降才可用。")


if __name__ == "__main__":
    main()
