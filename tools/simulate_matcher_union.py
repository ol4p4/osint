# -*- coding: utf-8 -*-
r"""simulate_matcher_union.py - 匹配策略对比：fallback vs union（2026-09-30，只读）

**问题**：`link_intel_hyp` 当前是 **fallback** 结构——TF-IDF 有命中（>=0.12）就
**完全不看** DOMAIN。但实测（tools/calibrate_tfidf_threshold.py）：

    DOMAIN  AUC = 0.822   （更高）
    TF-IDF  AUC = 0.709

    且 DOMAIN 对 18.2% 的真信号完全无分（这些只能靠 TF-IDF 捞）

即两者**互补**：DOMAIN 强但覆盖不全，TF-IDF 弱但覆盖面广。fallback 结构让
TF-IDF 的弱命中**屏蔽**掉 DOMAIN 的强命中，是结构性浪费。

**本工具**对比四种策略在同一标注集（JEV gate 作真值）上的召回/精确：
  A. 现状：TF-IDF@0.12 否则 DOMAIN@0.34
  B. 降门槛：TF-IDF@0.06 否则 DOMAIN@0.34
  C. 联合：TF-IDF@0.06 ∪ DOMAIN@0.34
  D. 联合：TF-IDF@0.12 ∪ DOMAIN@0.34

**只读，不写盘。**

用法：
    python tools/simulate_matcher_union.py
"""
import bisect
import glob
import hashlib
import json
import math
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs

GATE_SIGNAL = 0.15
GATE_NOISE = 0.08
DOMAIN_MIN = 0.34   # 生产值 DOMAIN_MIN_SCORE


def evidence_key(date, summary):
    raw = str(date or "") + "|" + str(summary or "")[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def load_intel_index():
    idx = {}
    files = sorted(glob.glob(str(BASE / "intel_2026*.jsonl")))[-34:]
    for f in files:
        if "raw" in Path(f).name or "final" in Path(f).name:
            continue
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            date = str(d.get("published_at") or d.get("date") or "")[:10]
            summary = d.get("cn_title") or d.get("title") or ""
            if date and summary:
                idx[evidence_key(date, summary)] = d
    return idx


def build_idf(docs, min_df=2):
    df = {}
    for toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    return {t: math.log(n / c) for t, c in df.items() if c >= min_df}


def vec(toks, idf):
    w = {}
    for t in toks:
        i = idf.get(t)
        if i is not None:
            w[t] = w.get(t, 0.0) + i
    norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
    return {t: v / norm for t, v in w.items()}


def cosine(a, b):
    if not a or not b:
        return 0.0
    x, y = (a, b) if len(a) <= len(b) else (b, a)
    return sum(w * y.get(k, 0.0) for k, w in x.items())


def hyp_doc(h):
    ind = " ".join(str(i.get("name", "")) for i in (h.get("indicators") or [])
                   if isinstance(i, dict))
    return " ".join([str(h.get("title", "")), str(h.get("core_claim") or ""),
                     str(h.get("rationale") or ""),
                     str(h.get("falsification_criteria") or "")[:200], ind])


def evaluate(name, matched_pairs, pairs):
    """matched_pairs: set of (i, h) 判为匹配；返回混淆矩阵指标"""
    pos = [(i, h) for i, h, g in pairs if g >= GATE_SIGNAL]
    neg = [(i, h) for i, h, g in pairs if g < GATE_NOISE]
    tp = sum(1 for p in pos if p in matched_pairs)
    fp = sum(1 for p in neg if p in matched_pairs)
    rec = tp / len(pos) if pos else 0.0
    pre = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
    print(f"  {name:<34} 召回 {rec:>6.1%}  精确 {pre:>6.1%}  F1 {f1:.3f}  "
          f"(TP {tp}/{len(pos)}, FP {fp}/{len(neg)})")
    return rec, pre, f1


def main():
    m = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))
    rows = m.get("evidence") or []
    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]
    major_ids = {h["id"] for h in majors}

    intel_idx = load_intel_index()
    pairs = []
    for r in rows:
        diag = r.get("diagnosis") or {}
        key = str(r.get("key") or "")
        if not isinstance(diag, dict) or key not in intel_idx:
            continue
        for hid, d in diag.items():
            if hid in major_ids and isinstance(d, dict) and d.get("gate") is not None:
                pairs.append((key, hid, float(d["gate"])))
    used_ids = sorted({p[0] for p in pairs})
    print(f"矩阵 {len(rows)} 条 | 有效对 {len(pairs)} | 涉及情报 {len(used_ids)} 条\n")

    hyp_docs = [cs._tokens(hyp_doc(h)) for h in majors]
    intel_docs = [cs._tokens(cs._item_text(intel_idx[i])) for i in used_ids]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = {h["id"]: vec(d, idf) for d, h in zip(hyp_docs, majors)}
    ivs = {i: vec(d, idf) for d, i in zip(intel_docs, used_ids)}

    # DOMAIN 分数（同生产参数）
    from link_intel_hyp import DOMAIN_MAP, match_intel_to_hyp
    all_kws = {kw.lower() for kws in DOMAIN_MAP.values() for kw in kws}
    n_docs = len(used_ids) or 1
    dfd = {kw: 0 for kw in all_kws}
    for i in used_ids:
        txt = cs._item_text(intel_idx[i]).lower()
        for kw in all_kws:
            if kw in txt:
                dfd[kw] += 1
    kw_idf = {kw: math.log(n_docs / (1.0 + max(c, 1))) for kw, c in dfd.items()}
    dom_of = {}
    for i in used_ids:
        for mt in match_intel_to_hyp(intel_idx[i], majors, min_score=0.0, kw_idf=kw_idf):
            dom_of[(i, mt["hyp_id"])] = float(mt.get("relevance_score") or 0)

    # TF-IDF 分数
    tf_of = {}
    for i in used_ids:
        for hid in major_ids:
            tf_of[(i, hid)] = cosine(ivs[i], hvs[hid])

    print("策略对比（正样本=JEV gate>=0.15，负样本=gate<0.08）:")
    results = {}

    # A. 现状：tfidf@0.12 否则 domain@0.34（按 intel 逐条判定是否走 fallback）
    matched = set()
    for i in used_ids:
        tf_hits = {hid for hid in major_ids if tf_of[(i, hid)] >= 0.12}
        if tf_hits:
            matched |= {(i, hid) for hid in tf_hits}
        else:
            matched |= {(i, hid) for hid in major_ids
                        if dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}
    results["A"] = evaluate("A. 现状 TFIDF@0.12 else DOMAIN@0.34", matched, pairs)

    # B. 只降门槛，仍是 fallback
    matched = set()
    for i in used_ids:
        tf_hits = {hid for hid in major_ids if tf_of[(i, hid)] >= 0.06}
        if tf_hits:
            matched |= {(i, hid) for hid in tf_hits}
        else:
            matched |= {(i, hid) for hid in major_ids
                        if dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}
    results["B"] = evaluate("B. fallback TFIDF@0.06 else DOMAIN", matched, pairs)

    # C. 联合：TFIDF@0.06 ∪ DOMAIN@0.34
    matched = {(i, hid) for i in used_ids for hid in major_ids
               if tf_of[(i, hid)] >= 0.06 or dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}
    results["C"] = evaluate("C. union TFIDF@0.06 | DOMAIN@0.34", matched, pairs)

    # D. 联合：TFIDF@0.12 ∪ DOMAIN@0.34
    matched = {(i, hid) for i in used_ids for hid in major_ids
               if tf_of[(i, hid)] >= 0.12 or dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}
    results["D"] = evaluate("D. union TFIDF@0.12 | DOMAIN@0.34", matched, pairs)

    # E. 仅 DOMAIN（看 TF-IDF 的边际贡献）
    matched = {(i, hid) for i in used_ids for hid in major_ids
               if dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}
    results["E"] = evaluate("E. 仅 DOMAIN@0.34（无 TF-IDF）", matched, pairs)

    # F. 仅 TF-IDF@0.06（看 DOMAIN 的边际贡献）
    matched = {(i, hid) for i in used_ids for hid in major_ids
               if tf_of[(i, hid)] >= 0.06}
    results["F"] = evaluate("F. 仅 TFIDF@0.06（无 DOMAIN）", matched, pairs)

    print("\n边际贡献分析:")
    a, e, f = results["A"], results["E"], results["F"]
    print(f"  TF-IDF 独有能力：仅 TF-IDF 能捞到的正样本 = "
          f"{f[0]*100:.1f}% (F) - 被 DOMAIN 覆盖部分")
    print(f"  DOMAIN 独有能力：仅 DOMAIN 能捞到的正样本 = {e[0]*100:.1f}% (E)")
    print(f"  联合(C) 相对现状(A) 召回提升: {(results['C'][0]-a[0])*100:+.1f}pp，"
          f"精确变化 {(results['C'][1]-a[1])*100:+.1f}pp")

    # 候选池规模（cap=6/假设/日 下，池子越大越可能挤出高价值候选）
    print("\n候选池规模（每条情报平均匹配假设数）:")
    for tag, rule in [("现状 A", lambda i: {hid for hid in major_ids if tf_of[(i, hid)] >= 0.12}
                                          or {hid for hid in major_ids if dom_of.get((i, hid), 0.0) >= DOMAIN_MIN}),
                      ("联合 C", lambda i: {hid for hid in major_ids
                                            if tf_of[(i, hid)] >= 0.06 or dom_of.get((i, hid), 0.0) >= DOMAIN_MIN})]:
        tot = sum(len(rule(i)) for i in used_ids)
        print(f"  {tag}: 总匹配对 {tot}，平均 {tot/len(used_ids):.2f} 假设/条")


if __name__ == "__main__":
    main()
