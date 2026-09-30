# -*- coding: utf-8 -*-
r"""threshold_scan.py - 在新分词空间下重新校准 TF-IDF 阈值（2026-09-30，只读）

**动机**：`TFIDF_MIN_SIM=0.12` 是在字符 2-gram 空间校准的经验值。分词器换成
SP-unigram 后，token 粒度变粗（"解放军" 1 个 token vs "解放"+"放军" 2 个），
向量维度和权重分布都变了，**同一阈值在新空间下含义不同**。

本工具测量新空间下的余弦分数分布，并给出候选阈值下的证据量，
供人工选择（不做自动决定）。

用法：
    python tools/threshold_scan.py
    python tools/threshold_scan.py --corpus-days 3
"""
import argparse
import glob
import json
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs


def load_corpus(days):
    files = sorted(glob.glob(str(BASE / "intel_2026*.jsonl")))[-days - 2:]
    items, seen = [], set()
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
            iid = d.get("id")
            if iid and iid in seen:
                continue
            if iid:
                seen.add(iid)
            items.append(d)
    return items


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-days", type=int, default=3)
    args = ap.parse_args()

    items = load_corpus(args.corpus_days)
    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]
    print(f"语料 {len(items)} 条 | major 假设 {len(majors)} 个")
    sp = cs._get_sp()
    print(f"分词器: {'SP-unigram' if sp else '2-gram(降级)'}\n")

    hyp_docs = [cs._tokens(hyp_doc(h)) for h in majors]
    intel_docs = [cs._tokens(cs._item_text(it)) for it in items]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = [vec(d, idf) for d in hyp_docs]
    ivs = [vec(d, idf) for d in intel_docs]

    # 每条情报对每个 major 的余弦；记录"最佳匹配分"
    best_scores = []
    per_hyp = Counter()
    for iv in ivs:
        sims = [(cosine(iv, hv), h["id"]) for hv, h in zip(hvs, majors)]
        sims.sort(reverse=True)
        best_scores.append(sims[0][0] if sims else 0.0)

    best_scores.sort()
    n = len(best_scores)
    if n == 0:
        print("无语料")
        return

    def pct(p):
        return best_scores[min(int(n * p), n - 1)]

    print("最佳匹配分分布（每条情报对 6 个 major 取最高）:")
    print(f"  min={best_scores[0]:.4f}  p25={pct(.25):.4f}  p50={pct(.50):.4f}  "
          f"p75={pct(.75):.4f}  p90={pct(.90):.4f}  p95={pct(.95):.4f}  max={best_scores[-1]:.4f}")

    print("\n阈值扫描（新空间）:")
    print(f"{'阈值':>8} {'命中条数':>10} {'占比':>8} {'相对 p50 倍数':>14}")
    for thr in [0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30]:
        hit = sum(1 for s in best_scores if s >= thr)
        ratio = thr / pct(.50) if pct(.50) > 0 else 0
        print(f"{thr:>8.2f} {hit:>10} {100*hit/n:>7.2f}% {ratio:>13.2f}x")

    # 每个假设各自的最佳命中分布（用于看哪些假设"从不被命中"）
    print("\n各假设被命中情况（>=0.12）:")
    for hv, h in zip(hvs, majors):
        sims = [cosine(iv, hv) for iv in ivs]
        hit = sum(1 for s in sims if s >= 0.12)
        mx = max(sims) if sims else 0.0
        print(f"  {h['id']} {str(h.get('title',''))[:28]:<30} 命中 {hit:>4} 条  最高 {mx:.3f}")


if __name__ == "__main__":
    main()
