# -*- coding: utf-8 -*-
r"""simulate_cap_selection.py - cap 择优质量模拟（2026-09-30，只读）

**问题**：`EVIDENCE_DAILY_CAP=6` 决定每个假设每天真正入库的 6 条证据。
Pass2 排序 `_sel_key` 给 TF-IDF 命中**无条件优先**（`0 if tfidf else 1`），
所以"放低 TF-IDF 门槛 / 改 union 结构"会改变**哪 6 条被选中**——
可能挤出更好的 DOMAIN 候选。

**本工具**直接模拟 Pass2 的 cap 择优，测三种配置下**选中的证据质量**：
  A. 现状：TFIDF@0.12 fallback DOMAIN@0.34，TF-IDF 优先排序
  B. union@0.06：TFIDF@0.06 ∪ DOMAIN@0.34，TF-IDF 优先排序
  C. union@0.06 + 统一分排序：按 DOMAIN 分优先（AUC 更高）

质量指标：选中的证据里真信号（gate>=0.15）占比 / 平均 gate / 覆盖的真信号总数。

**只读，不写盘。**

用法：
    python tools/simulate_cap_selection.py
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
CAP = 6   # 生产值 EVIDENCE_DAILY_CAP
ACH_ELIGIBLE_MIN = 0.4   # 生产值 ACH_ELIGIBLE_MIN（DOMAIN 兜底进 ACH 队列的分数线）


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


def main():
    m = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))
    rows = m.get("evidence") or []
    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]
    major_ids = {h["id"] for h in majors}

    intel_idx = load_intel_index()
    gate_of = {}   # (key, hyp_id) -> gate
    for r in rows:
        diag = r.get("diagnosis") or {}
        key = str(r.get("key") or "")
        if not isinstance(diag, dict) or key not in intel_idx:
            continue
        for hid, d in diag.items():
            if hid in major_ids and isinstance(d, dict) and d.get("gate") is not None:
                gate_of[(key, hid)] = float(d["gate"])
    used_ids = sorted({k for k, _ in gate_of})
    print(f"矩阵 {len(rows)} | 有效对 {len(gate_of)} | 情报 {len(used_ids)} 条 | "
          f"真信号 {sum(1 for g in gate_of.values() if g >= GATE_SIGNAL)}")

    # 情报日期（用于按日分桶——cap 是"每假设每日"）
    date_of = {}
    for i in used_ids:
        date_of[i] = str(intel_idx[i].get("published_at") or "")[:10]

    hyp_docs = [cs._tokens(hyp_doc(h)) for h in majors]
    intel_docs = [cs._tokens(cs._item_text(intel_idx[i])) for i in used_ids]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = {h["id"]: vec(d, idf) for d, h in zip(hyp_docs, majors)}
    ivs = {i: vec(d, idf) for d, i in zip(intel_docs, used_ids)}

    # DOMAIN 分数
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

    # ---- 构造候选（按配置）----
    def build_candidates(tfidf_thr, union):
        """返回 [(hyp_id, i, method, score)]"""
        out = []
        for i in used_ids:
            tf = {h["id"]: cosine(ivs[i], hvs[h["id"]]) for h in majors}
            tf_hits = {hid for hid, s in tf.items() if s >= tfidf_thr}
            dom_hits = {hid for hid, s in dom_of.items() if hid and False}  # placeholder
            dom_hits = {h["id"] for h in majors
                        if dom_of.get((i, h["id"]), 0.0) >= DOMAIN_MIN}
            if union:
                for hid in tf_hits:
                    out.append((hid, i, "tfidf", tf[hid]))
                for hid in dom_hits:
                    out.append((hid, i, "domain", dom_of.get((i, hid), 0.0)))
            else:
                if tf_hits:
                    for hid in tf_hits:
                        out.append((hid, i, "tfidf", tf[hid]))
                else:
                    for hid in dom_hits:
                        out.append((hid, i, "domain", dom_of.get((i, hid), 0.0)))
        return out

    def simulate(cands, order):
        """模拟 Pass2 cap：按 order 排序，每假设每日取前 CAP 条（去重后）"""
        if order == "tfidf_first":
            key = lambda c: (0 if c[2] == "tfidf" else 1, -c[3])
        elif order == "domain_first":
            key = lambda c: (0 if c[2] == "domain" else 1, -c[3])
        else:  # score_first：DOMAIN 分与 TF-IDF 分归一后比较（粗对齐）
            key = lambda c: (-(c[3] / DOMAIN_MIN if c[2] == "domain" else c[3] / tfidf_thr),)
        selected = []
        cnt = defaultdict(int)
        seen = set()
        for hid, i, meth, sc in sorted(cands, key=key):
            k = (hid, i)
            if k in seen:
                continue
            day = (hid, date_of[i])
            if cnt[day] >= CAP:
                continue
            seen.add(k)
            cnt[day] += 1
            selected.append((hid, i, meth, sc))
        return selected

    print("\n" + "=" * 70)
    print("cap 择优模拟（每假设每日 top-6）")
    print("=" * 70)

    configs = [
        ("A 现状 fallback@0.12 / tfidf优先", 0.12, False, "tfidf_first"),
        ("B union@0.06 / tfidf优先", 0.06, True, "tfidf_first"),
        ("C union@0.06 / domain优先", 0.06, True, "domain_first"),
        ("D union@0.12 / domain优先", 0.12, True, "domain_first"),
        ("E union@0.12 / tfidf优先", 0.12, True, "tfidf_first"),
    ]
    summary = {}
    for name, thr, uni, order in configs:
        cands = build_candidates(thr, uni)
        sel = simulate(cands, order)
        gates = [gate_of.get((i, hid), 0.0) for hid, i, _m, _s in sel]
        n_sig = sum(1 for g in gates if g >= GATE_SIGNAL)
        n_noise = sum(1 for g in gates if g < GATE_NOISE)
        avg = sum(gates) / len(gates) if gates else 0.0
        # 覆盖的真信号总数（去重：同一条真信号被多假设选中只算一次）
        covered = {(i, hid) for hid, i, _m, _s in sel if gate_of.get((i, hid), 0) >= GATE_SIGNAL}
        # ACH 准入资格（ach_eligible 规则：tfidf 一律合格 / domain 需 >=0.4）
        elig = sum(1 for _h, _i, meth, sc in sel
                   if meth == "tfidf" or sc >= ACH_ELIGIBLE_MIN)
        summary[name] = (len(sel), n_sig, n_noise, avg, len(covered), len(cands), elig)
        print(f"\n[{name}]")
        print(f"  候选池 {len(cands)} 条 → 选中 {len(sel)} 条")
        print(f"  选中里真信号 {n_sig} ({100*n_sig/max(len(sel),1):.1f}%) | "
              f"噪声 {n_noise} ({100*n_noise/max(len(sel),1):.1f}%) | 平均 gate {avg:.3f}")
        print(f"  覆盖真信号（去重）{len(covered)} | ACH 准入合格 {elig} "
              f"({100*elig/max(len(sel),1):.1f}%)")

    print("\n" + "=" * 70)
    print("关键对比（以 A 现状为基准）:")
    a = summary["A 现状 fallback@0.12 / tfidf优先"]
    for name in list(summary)[1:]:
        b = summary[name]
        print(f"  {name[:28]:<30} 真信号 {b[1]-a[1]:+4d} 条  "
              f"噪声 {b[2]-a[2]:+5d} 条  平均 gate {b[3]-a[3]:+.3f}  "
              f"覆盖 {b[4]-a[4]:+3d}  准入 {b[6]-a[6]:+4d}")


if __name__ == "__main__":
    main()
