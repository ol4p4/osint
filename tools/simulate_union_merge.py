# -*- coding: utf-8 -*-
r"""simulate_union_merge.py - union 结构的忠实模拟（2026-09-30，只读）

**要回答的问题**：把 Pass 1 的 `if not matches:` fallback 结构改为 union，
在**真实的 merge 语义**下（按 hyp_id 合并，同时命中时保留 tfidf 方法以保住
ach_eligible 资格），端到端收益是多少？

**上一轮模拟的缺陷**（tools/simulate_matcher_union.py）：把 tfidf 与 domain
当作**两条独立候选**参与排序，于是同一 (情报,假设) 对可能出现两次、由排序
决定用哪个方法——现实中应当**合并成一条**，且同时命中时 method 取 tfidf
（否则会丢失 `ach_eligible` 资格，因为 DOMAIN 需 ≥0.4 才算合格）。

**地面真值**：矩阵里 9420 个已被 JEV 判定的（证据,假设）对，gate ≥0.15 为真信号。

用法：
    python tools/simulate_union_merge.py
"""
import glob
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs

GATE_SIGNAL = 0.15
GATE_NOISE = 0.08
DOMAIN_MIN = 0.34
TFIDF_MIN = 0.12
CAP = 6
ACH_ELIGIBLE_MIN = 0.4


def evidence_key(date, summary):
    raw = str(date or "") + "|" + str(summary or "")[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def load_intel_index():
    idx = {}
    for f in sorted(glob.glob(str(BASE / "intel_2026*.jsonl")))[-34:]:
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


def main():
    m = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))
    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]
    major_ids = {h["id"] for h in majors}

    intel_idx = load_intel_index()
    gate_of = {}
    date_of = {}
    for r in m["evidence"]:
        d = r.get("diagnosis") or {}
        key = str(r.get("key") or "")
        if not isinstance(d, dict) or key not in intel_idx:
            continue
        for hid, v in d.items():
            if hid in major_ids and isinstance(v, dict) and v.get("gate") is not None:
                gate_of[(key, hid)] = float(v["gate"])
        date_of[key] = str(r.get("date") or "")[:10]

    used_ids = sorted({k for k, _ in gate_of})
    pos = {(i, h) for (i, h), g in gate_of.items() if g >= GATE_SIGNAL}
    neg = {(i, h) for (i, h), g in gate_of.items() if g < GATE_NOISE}
    print(f"有效对 {len(gate_of)} | 情报 {len(used_ids)} 条 | "
          f"真信号 {len(pos)} | 噪声 {len(neg)}")

    hyp_docs = [cs._tokens(hyp_doc(h)) for h in majors]
    intel_docs = [cs._tokens(cs._item_text(intel_idx[i])) for i in used_ids]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = {h["id"]: vec(d, idf) for d, h in zip(hyp_docs, majors)}
    ivs = {i: vec(d, idf) for d, i in zip(intel_docs, used_ids)}

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

    # ---- 构造候选：两种结构 ----
    def build(structure):
        """返回 {hyp_id: [(key, method, score)]}"""
        out = defaultdict(list)
        for i in used_ids:
            tf = {h["id"]: cosine(ivs[i], hvs[h["id"]]) for h in majors}
            tf_hits = {hid: s for hid, s in tf.items() if s >= TFIDF_MIN}
            dom_hits = {h["id"]: dom_of.get((i, h["id"]), 0.0) for h in majors
                        if dom_of.get((i, h["id"]), 0.0) >= DOMAIN_MIN}
            if structure == "fallback":
                if tf_hits:
                    for hid, s in tf_hits.items():
                        out[hid].append((i, "tfidf", s))
                else:
                    for hid, s in dom_hits.items():
                        out[hid].append((i, "domain", s))
            else:  # union：合并，同时命中保留 tfidf 方法（保住 eligible 资格）
                for hid, s in tf_hits.items():
                    out[hid].append((i, "tfidf", s))
                for hid, s in dom_hits.items():
                    if hid not in tf_hits:
                        out[hid].append((i, "domain", s))
        return out

    def evaluate(tag, cand_by_hyp):
        """模拟 Pass2：每假设每日 cap=6，排序 = tfidf 优先 → relevance 降序"""
        picked = []
        for hid, cands in cand_by_hyp.items():
            by_day = defaultdict(list)
            for key, meth, sc in cands:
                by_day[date_of.get(key, "")].append((key, meth, sc))
            for day, items in by_day.items():
                items.sort(key=lambda x: (0 if x[1] == "tfidf" else 1, -x[2]))
                picked.extend((k, hid) for k, _m, _s in items[:CAP])
        ps = {p for p in picked if p in pos}
        ns = {p for p in picked if p in neg}
        rec = len(ps) / len(pos) if pos else 0
        pre = len(ps) / (len(ps) + len(ns)) if (len(ps) + len(ns)) else 0
        f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0
        # ACH 准入：tfidf 一律合格；domain 需 >= 0.4
        elig = 0
        for hid, cands in cand_by_hyp.items():
            by_day = defaultdict(list)
            for key, meth, sc in cands:
                by_day[date_of.get(key, "")].append((key, meth, sc))
            for day, items in by_day.items():
                items.sort(key=lambda x: (0 if x[1] == "tfidf" else 1, -x[2]))
                for _k, meth, sc in items[:CAP]:
                    if meth == "tfidf" or sc >= ACH_ELIGIBLE_MIN:
                        elig += 1
        print(f"\n[{tag}]")
        print(f"  入选 {len(picked)} 条 | 真信号 {len(ps)} | 噪声 {len(ns)} | "
              f"精确 {pre:.1%}")
        print(f"  召回 {rec:.1%}  F1 {f1:.3f}  ACH 准入合格 {elig} "
              f"({100*elig/max(len(picked),1):.1f}%)")
        return rec, pre, f1, len(ps), len(ns), elig

    print("\n" + "=" * 70)
    print("结构对比（真实 merge 语义 + Pass2 cap 模拟）")
    print("=" * 70)
    a = evaluate("A 现状 fallback", build("fallback"))
    b = evaluate("B union（合并，保留 tfidf 方法）", build("union"))

    print("\n" + "=" * 70)
    print("净收益 (B - A):")
    print(f"  召回 {a[0]:.1%} → {b[0]:.1%}  ({(b[0]-a[0])*100:+.1f}pp)")
    print(f"  精确 {a[1]:.1%} → {b[1]:.1%}  ({(b[1]-a[1])*100:+.1f}pp)")
    print(f"  F1   {a[2]:.3f} → {b[2]:.3f}  ({b[2]-a[2]:+.3f})")
    print(f"  真信号 {a[3]} → {b[3]}  ({b[3]-a[3]:+d})")
    print(f"  噪声   {a[4]} → {b[4]}  ({b[4]-a[4]:+d})")
    print(f"  准入   {a[5]} → {b[5]}  ({b[5]-a[5]:+d})")


if __name__ == "__main__":
    main()
