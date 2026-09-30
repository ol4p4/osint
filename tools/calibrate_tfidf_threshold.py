# -*- coding: utf-8 -*-
r"""calibrate_tfidf_threshold.py - 用 JEV 判定校准 TF-IDF 阈值（2026-09-30，只读）

**动机**：`TFIDF_MIN_SIM=0.12` 是 2-gram 空间的经验值。分词器换成 SP-unigram 后
token 粒度变粗，分数分布整体移动（实测 p50 0.029 / p95 0.098），
**同一阈值在新空间下含义不同**，必须重校。

**方法**：不用 9 条人工标注（样本太小），改用矩阵里 **5743 条已被 JEV 判定**的
证据作为地面真值：
  - 正样本 = 该证据对某 major 的 gate >= GATE_ABS_THRESHOLD（0.15，真信号）
  - 负样本 = gate < 0.08（噪声上界）
然后测 TF-IDF 余弦在这些样本上的区分度（AUC / precision-recall），
扫描候选阈值，选出在**保持 precision 前提下召回最高**的点。

**注意**：TF-IDF 是**预筛**层，目标是"别漏掉真信号"（高召回），
最终判定由 JEV 门控把关。所以阈值应偏宽松，而非追求 precision。

用法：
    python tools/calibrate_tfidf_threshold.py
"""
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

# 与 jev_client 保持一致的判据（真信号门槛 / 噪声上界）
GATE_SIGNAL = 0.15   # >= 此值 = 真信号（GATE_ABS_THRESHOLD）
GATE_NOISE = 0.08    # <  此值 = 噪声


def evidence_key(date, summary):
    """与 local/ach_matrix.evidence_key **完全一致**（否则对不上矩阵里的行）"""
    raw = str(date or "") + "|" + str(summary or "")[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def load_intel_index():
    """evidence_key -> 情报条目（近 32 天）"""
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
    hyp_by_id = {h["id"]: h for h in nodes}
    majors = [h for h in nodes if h.get("level") == "major"]
    major_ids = {h["id"] for h in majors}
    print(f"矩阵证据 {len(rows)} 条 | major {len(majors)} 个")

    intel_idx = load_intel_index()
    print(f"情报索引 {len(intel_idx)} 条（近 30 天）")

    # ---- 收集（证据, 假设）对及其 gate 标签 ----
    pairs = []   # (evidence_key, hyp_id, gate)
    missing = 0
    for r in rows:
        diag = r.get("diagnosis") or {}
        if not isinstance(diag, dict):
            continue
        key = str(r.get("key") or "")
        if key not in intel_idx:
            missing += 1
            continue
        for hid, d in diag.items():
            if hid not in major_ids or not isinstance(d, dict):
                continue
            g = d.get("gate")
            if g is None:
                continue
            pairs.append((key, hid, float(g)))
    print(f"有效（证据,假设）对: {len(pairs)} | 情报缺失跳过: {missing}")

    if not pairs:
        print("无可校准数据——矩阵里的证据 id 与本地情报对不上（可能语料已轮转）")
        return

    # ---- 只对有标签的样本算 TF-IDF ----
    used_ids = sorted({p[0] for p in pairs})
    print(f"涉及情报 {len(used_ids)} 条，计算 TF-IDF...")
    hyp_docs = [cs._tokens(hyp_doc(h)) for h in majors]
    intel_docs = [cs._tokens(cs._item_text(intel_idx[i])) for i in used_ids]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = {h["id"]: vec(d, idf) for d, h in zip(hyp_docs, majors)}
    ivs = {i: vec(d, idf) for d, i in zip(intel_docs, used_ids)}

    # ---- 构造正负样本 ----
    pos, neg = [], []
    for iid, hid, g in pairs:
        s = cosine(ivs[iid], hvs[hid])
        if g >= GATE_SIGNAL:
            pos.append(s)
        elif g < GATE_NOISE:
            neg.append(s)
    print(f"\n正样本(gate>={GATE_SIGNAL}): {len(pos)} 条")
    print(f"负样本(gate<{GATE_NOISE}): {len(neg)} 条")

    if not pos or not neg:
        print("正负样本不全，无法评估")
        return

    pos.sort()
    neg.sort()

    def pr(lst, p):
        return lst[min(int(len(lst) * p), len(lst) - 1)]

    print(f"\n正样本分数分布: min={pos[0]:.4f} p25={pr(pos,.25):.4f} "
          f"p50={pr(pos,.50):.4f} p75={pr(pos,.75):.4f} max={pos[-1]:.4f}")
    print(f"负样本分数分布: min={neg[0]:.4f} p25={pr(neg,.25):.4f} "
          f"p50={pr(neg,.50):.4f} p75={pr(neg,.75):.4f} p90={pr(neg,.90):.4f} max={neg[-1]:.4f}")

    # ---- AUC（排序质量）----
    # P(正样本分数 > 负样本分数)，用秩和计算
    import bisect
    neg_sorted = sorted(neg)
    ranks = 0.0
    for s in pos:
        lo = bisect.bisect_left(neg_sorted, s)
        hi = bisect.bisect_right(neg_sorted, s)
        ranks += lo + 0.5 * (hi - lo)
    auc = ranks / (len(pos) * len(neg))
    print(f"\nAUC = {auc:.4f}  (0.5=随机, 1.0=完美区分)")

    # ---- 阈值扫描 ----
    print(f"\n{'阈值':>7} {'召回':>9} {'精确':>9} {'F1':>8} {'命中正':>8} {'误报负':>8}")
    best = None
    for thr in [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20]:
        tp = sum(1 for s in pos if s >= thr)
        fp = sum(1 for s in neg if s >= thr)
        rec = tp / len(pos)
        pre = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
        print(f"{thr:>7.2f} {rec:>8.1%} {pre:>8.1%} {f1:>8.3f} {tp:>8} {fp:>8}")
        # 选择依据：召回 >= 80% 前提下 F1 最高
        if rec >= 0.80 and (best is None or f1 > best[1]):
            best = (thr, f1, rec, pre)
    if best:
        print(f"\n推荐阈值（召回>=80% 下 F1 最高）: {best[0]:.2f}  "
              f"F1={best[1]:.3f} 召回={best[2]:.1%} 精确={best[3]:.1%}")
    else:
        print("\n无阈值能达到 80% 召回——TF-IDF 词面匹配对这批样本区分力不足")

    # ---- 对照：DOMAIN 兜底的区分度（同一样本）----
    # 决策问题：TF-IDF 命中会**优先占用** cap 名额（Pass2 排序 TF-IDF 优先），
    # 放低门槛 = 更多弱 TF-IDF 候选挤掉 DOMAIN 候选。必须知道 DOMAIN 的区分度
    # 是否更低——否则是拿好的换差的。
    print("\n" + "=" * 62)
    print("DOMAIN 兜底对照（同一样本）")
    try:
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

        def _auc(p, n):
            if not p or not n:
                return float("nan")
            ns = sorted(n)
            r = 0.0
            for s in p:
                lo = bisect.bisect_left(ns, s)
                hi = bisect.bisect_right(ns, s)
                r += lo + 0.5 * (hi - lo)
            return r / (len(p) * len(n))

        dp = [dom_of.get((i, h), 0.0) for i, h, g in pairs if g >= GATE_SIGNAL]
        dn = [dom_of.get((i, h), 0.0) for i, h, g in pairs if g < GATE_NOISE]
        print(f"  DOMAIN AUC = {_auc(dp, dn):.4f}   vs   TF-IDF AUC = {auc:.4f}")
        print(f"  DOMAIN 零分占比: 正样本 {sum(1 for s in dp if s == 0) / len(dp):.1%} / "
              f"负样本 {sum(1 for s in dn if s == 0) / len(dn):.1%}"
              f"（零分=DOMAIN 完全没匹配上，只能靠 TF-IDF 捞）")
    except Exception as e:
        print(f"  DOMAIN 对照跳过: {e}")

    # ---- 分带正例率（最直观：每一段分数里真信号占多少）----
    base_rate = len(pos) / (len(pos) + len(neg))
    print(f"\n基线正例率（样本内）= {base_rate:.2%}；高于它说明该段有区分力")
    print(f"{'TF-IDF 分数带':>16} {'样本数':>8} {'真信号':>8} {'正例率':>8} {'相对基线':>10}")
    for lo, hi in [(0.0, 0.04), (0.04, 0.06), (0.06, 0.08), (0.08, 0.12),
                   (0.12, 0.20), (0.20, 1.01)]:
        sub = [(i, h, g) for i, h, g in pairs
               if lo <= cosine(ivs[i], hvs[h]) < hi and (g >= GATE_SIGNAL or g < GATE_NOISE)]
        if not sub:
            continue
        p = sum(1 for _, _, g in sub if g >= GATE_SIGNAL)
        rate = p / len(sub)
        print(f"{f'{lo:.2f}~{hi:.2f}':>16} {len(sub):>8} {p:>8} "
              f"{rate:>7.1%} {rate / base_rate:>9.1f}x")


if __name__ == "__main__":
    main()
