# -*- coding: utf-8 -*-
r"""verify_tokenizer_upgrade.py - 分词器升级的生产级 A/B（2026-09-30，只读）

**要回答的问题**：把 `_tokens` 从字符 2-gram 换成 SP-unigram 后，
**生产匹配结构下**（TFIDF@0.12 否则 DOMAIN@0.34）的召回/精确有没有改善？

**为什么需要**：分词器级指标（语料命中率）不等于管线级效果。TF-IDF 只是预筛，
真正的候选由"TF-IDF 命中则跳过 DOMAIN"的结构决定——分词器变强会改变
多少条目走 TF-IDF 分支，进而改变整个候选池构成。

**地面真值**：矩阵里 5743 条已被 JEV 判定的证据（gate 值）——比人工标注可靠。
  - 正样本 = gate >= 0.15（真信号）
  - 负样本 = gate <  0.08（噪声上界）

**附带**：聚类丢弃对的细化分类（用标题字符相似度区分"真同事件被拆"vs"模板误聚被修"）。

用法：
    python tools/verify_tokenizer_upgrade.py
"""
import copy
import glob
import hashlib
import json
import math
import re
import sys
from difflib import SequenceMatcher
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

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[a-zA-Z]{2,}")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def tok_2gram(text):
    """升级前的生产分词（机械字符 2-gram）"""
    if not text:
        return []
    text = str(text)
    chars = _CJK.findall(text)
    toks = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    toks.extend(w.lower() for w in _LAT.findall(text))
    toks.extend(_NUM.findall(text))
    return toks


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


def run_structure(tokfn, used_ids, intel_idx, majors, dom_of, mode="fallback"):
    """按生产结构产匹配对。

    mode="fallback"：TF-IDF@0.12 命中则**完全跳过** DOMAIN（现状）
    mode="union"   ：TF-IDF@0.12 **并集** DOMAIN@0.34（候选互补）
    """
    hyp_docs = [tokfn(hyp_doc(h)) for h in majors]
    intel_docs = [tokfn(cs._item_text(intel_idx[i])) for i in used_ids]
    idf = build_idf(hyp_docs + intel_docs)
    hvs = {h["id"]: vec(d, idf) for d, h in zip(hyp_docs, majors)}
    ivs = {i: vec(d, idf) for d, i in zip(intel_docs, used_ids)}

    matched = set()
    tfidf_branch = 0
    for i in used_ids:
        tf_hits = {h["id"] for h in majors if cosine(ivs[i], hvs[h["id"]]) >= TFIDF_MIN}
        dom_hits = {h["id"] for h in majors
                    if dom_of.get((i, h["id"]), 0.0) >= DOMAIN_MIN}
        if mode == "union":
            matched |= {(i, hid) for hid in (tf_hits | dom_hits)}
            if tf_hits:
                tfidf_branch += 1
        else:
            if tf_hits:
                tfidf_branch += 1
                matched |= {(i, hid) for hid in tf_hits}
            else:
                matched |= {(i, hid) for hid in dom_hits}
    return matched, tfidf_branch, ivs, hvs


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
    pos = [(i, h) for i, h, g in pairs if g >= GATE_SIGNAL]
    neg = [(i, h) for i, h, g in pairs if g < GATE_NOISE]
    print(f"矩阵 {len(rows)} 条 | 有效对 {len(pairs)} | 情报 {len(used_ids)} 条")
    print(f"正样本 {len(pos)} | 负样本 {len(neg)}\n")

    # DOMAIN 分数（与分词器无关，只算一次）
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

    print("=" * 68)
    print("生产结构 A/B（分词器 × 匹配结构 全组合）")
    print("=" * 68)
    results = {}
    for mode in ("fallback", "union"):
        for tag, tokfn in [("2-gram", tok_2gram), ("SP-unigram", cs._tokens)]:
            matched, tfidf_branch, _, _ = run_structure(
                tokfn, used_ids, intel_idx, majors, dom_of, mode=mode)
            tp = sum(1 for p in pos if p in matched)
            fp = sum(1 for p in neg if p in matched)
            rec = tp / len(pos)
            pre = tp / (tp + fp) if (tp + fp) else 0.0
            f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else 0.0
            key = f"{mode}/{tag}"
            results[key] = (rec, pre, f1, tp, fp, tfidf_branch)
            print(f"\n[{key}]")
            print(f"  走 TF-IDF 分支的条目: {tfidf_branch}/{len(used_ids)} "
                  f"({100*tfidf_branch/len(used_ids):.1f}%)")
            print(f"  召回 {rec:.1%}  精确 {pre:.1%}  F1 {f1:.3f}  "
                  f"(TP {tp}/{len(pos)}, FP {fp}/{len(neg)})")

    a = results["fallback/2-gram"]
    b = results["fallback/SP-unigram"]
    c = results["union/2-gram"]
    d = results["union/SP-unigram"]
    print("\n" + "=" * 68)
    print("关键对比:")
    print(f"  A 现状(2gram/fallback): 召回 {a[0]:.1%} 精确 {a[1]:.1%} F1 {a[2]:.3f}")
    print(f"  B 仅换分词器(B vs A):    召回 {b[0]-a[0]:+.1f}pp 精确 {b[1]-a[1]:+.1f}pp F1 {b[2]-a[2]:+.3f}")
    print(f"  C 仅改结构(C vs A):      召回 {c[0]-a[0]:+.1f}pp 精确 {c[1]-a[1]:+.1f}pp F1 {c[2]-a[2]:+.3f}")
    print(f"  D 两者都改(D vs A):      召回 {d[0]-a[0]:+.1f}pp 精确 {d[1]-a[1]:+.1f}pp F1 {d[2]-a[2]:+.3f}")
    print(f"     (D 绝对: 召回 {d[0]:.1%} 精确 {d[1]:.1%} F1 {d[2]:.3f}  "
          f"多捞真信号 {d[3]-a[3]} 条，多引入噪声 {d[4]-a[4]} 条)")

    # ---- 聚类丢弃对细化分类 ----
    print("\n" + "=" * 68)
    print("聚类回归细化（标题字符相似度区分真伪）")
    print("=" * 68)
    items, seen = [], set()
    for f in sorted(glob.glob(str(BASE / "intel_2026*.jsonl")), reverse=True)[:5]:
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
    print(f"聚类语料 {len(items)} 条")

    def cluster_with(use_sp):
        ot, op = cs._SP_TRIED, cs._SP_PROCESSOR
        if use_sp:
            cs._SP_TRIED, cs._SP_PROCESSOR = False, None
            cs._get_sp()
        else:
            cs._SP_TRIED, cs._SP_PROCESSOR = True, None
        try:
            data = copy.deepcopy(items)
            cs.assign_story_ids(data)
            cl = {}
            for it in data:
                sid = it.get("story_id")
                if sid:
                    cl.setdefault(sid, []).append(it)
            return cl, data
        finally:
            cs._SP_TRIED, cs._SP_PROCESSOR = ot, op

    cl2, _ = cluster_with(False)
    clsp, dsp = cluster_with(True)

    def pr(cl):
        s = set()
        for _sid, ms in cl.items():
            ids = sorted(str(x.get("id")) for x in ms)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    s.add((ids[i], ids[j]))
        return s

    p2, psp = pr(cl2), pr(clsp)
    dropped, added = p2 - psp, psp - p2
    byid = {str(x.get("id")): x for x in dsp}

    def ttl(i):
        x = byid.get(i, {})
        return str(x.get("cn_title") or x.get("title") or "")

    def sim(x, y):
        return SequenceMatcher(None, x, y).ratio()

    # 真同事件被拆 = 标题高度相似却不同簇
    real_loss = [(a, b) for a, b in dropped if sim(ttl(a)[:40], ttl(b)[:40]) >= 0.75]
    correct_split = len(dropped) - len(real_loss)
    real_gain = [(a, b) for a, b in added if sim(ttl(a)[:40], ttl(b)[:40]) >= 0.75]
    print(f"\n  2-gram 同簇对 {len(p2)} → SP 同簇对 {len(psp)}")
    print(f"  被拆散 {len(dropped)} 对：其中标题高相似（真同事件被拆）{len(real_loss)}，"
          f"模板误聚被修正 {correct_split}")
    print(f"  新合并 {len(added)} 对：其中标题高相似（真同事件被捞回）{len(real_gain)}，"
          f"标题不同（疑似新误聚）{len(added)-len(real_gain)}")
    if real_loss:
        print("\n  真同事件被拆样例（应关注）:")
        for a, b in real_loss[:6]:
            print(f"    A: {ttl(a)[:52]}")
            print(f"    B: {ttl(b)[:52]}")
    new_false = [(a, b) for a, b in added if sim(ttl(a)[:40], ttl(b)[:40]) < 0.75]
    if new_false:
        print("\n  新误聚样例（应关注）:")
        for a, b in new_false[:6]:
            print(f"    A: {ttl(a)[:52]}")
            print(f"    B: {ttl(b)[:52]}")


if __name__ == "__main__":
    main()
