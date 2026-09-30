# -*- coding: utf-8 -*-
r"""find_ranking_signal2.py - 补充排序信号（2026-09-30，只读）

**背景**：`kw_hits*10 + body_len/100` 已上线（回测 +112%），但 **62.9% 的情报
`keywords_hit` 为空**（金十快讯类未走关键词打分），这些条目只能靠 body_len 排序，
区分力弱。

**本工具**：为"kw 为空"的条目寻找替代信号。候选：
  - tfidf 余弦（TF-IDF 匹配时的相似度，已有字段 relevance 里混着 domain 分）
  - 情报的 base_score / final_score
  - 标题/正文长度
  - 是否含数字（含具体数值的新闻往往更实质）

**方法**：只用 kw 为空的分组，测各信号 AUC；再测组合。

用法：
    python tools/find_ranking_signal2.py
"""
import bisect
import glob
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"

GATE_SIGNAL = 0.15
GATE_NOISE = 0.08

_NUM = re.compile(r"\d")


def ekey(d, s):
    return "ev_" + hashlib.sha256((str(d) + "|" + str(s)[:80]).encode("utf-8")).hexdigest()[:10]


def auc(pos, neg):
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

    intel = {}
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
                intel[d["id"]] = d

    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]

    # 只收 kw 为空的样本
    samples = []
    for h in majors:
        for e in (h.get("evidence_log") or []):
            k = ekey(e.get("date"), e.get("summary"))
            g = gate.get(k)
            if g is None:
                continue
            iid = (e.get("intel_ids") or [None])[0]
            it = intel.get(iid)
            if not it:
                continue
            if (it.get("keywords_hit") or []):
                continue   # 只看 kw 为空组
            body = str(it.get("cn_summary") or it.get("content_preview")
                       or it.get("content") or "")
            title = str(it.get("cn_title") or it.get("title") or "")
            samples.append(({
                "relevance": float(e.get("relevance") or 0),
                "base_score": float(it.get("base_score") or 0),
                "final_score": float(it.get("final_score") or 0),
                "body_len": len(body),
                "title_len": len(title),
                "has_num_body": 1.0 if _NUM.search(body) else 0.0,
                "has_num_title": 1.0 if _NUM.search(title) else 0.0,
                "n_domains": float(len(e.get("domains") or [])),
                "story_size": float(it.get("story_size") or 0),
            }, g))
    print(f"kw 为空的已诊断样本: {len(samples)} 条")
    if len(samples) < 50:
        print("样本过少")
        return

    pos = [s for s, g in samples if g >= GATE_SIGNAL]
    neg = [s for s, g in samples if g < GATE_NOISE]
    print(f"其中真信号 {len(pos)} | 噪声 {len(neg)}\n")

    print("=" * 66)
    print("kw 为空组内各信号的预测力")
    print("=" * 66)
    results = {}
    for name in samples[0][0]:
        p = [s[name] for s in pos]
        n = [s[name] for s in neg]
        if max(p + n) == min(p + n):
            print(f"  {name:<14} 恒定值（无区分力）")
            continue
        a = auc(p, n)
        results[name] = a
        vals = [s[name] for s, _ in samples]
        c = Counter(round(v, 4) for v in vals)
        tie = 100 * c.most_common(1)[0][1] / len(vals)
        print(f"  {name:<14} AUC={a:.4f}  并列 {tie:.1f}%")

    ranked = sorted(((a, n) for n, a in results.items() if a == a), reverse=True)
    if ranked:
        print(f"\n最佳: {ranked[0][1]} (AUC={ranked[0][0]:.4f})")

    # 组合
    print("\n" + "=" * 66)
    print("组合信号")
    print("=" * 66)

    def combo(fn, label):
        p = [fn(s) for s in pos]
        n = [fn(s) for s in neg]
        a = auc(p, n)
        vals = [fn(s) for s, _ in samples]
        c = Counter(round(v, 6) for v in vals)
        tie = 100 * c.most_common(1)[0][1] / len(vals)
        print(f"  {label:<38} AUC={a:.4f}  并列 {tie:.1f}%")
        return a

    combo(lambda s: s["body_len"], "body_len")
    combo(lambda s: s["body_len"] + s["base_score"] * 100, "body_len + base_score*100")
    combo(lambda s: s["body_len"] + s["relevance"] * 1000, "body_len + relevance*1000")
    combo(lambda s: s["body_len"] + s["relevance"] * 1000 + s["base_score"] * 100,
          "body_len + relevance*1000 + base*100")
    combo(lambda s: s["relevance"] * 1000 + s["base_score"] * 100,
          "relevance*1000 + base_score*100")
    combo(lambda s: s["has_num_body"] * 50 + s["body_len"], "has_num*50 + body_len")


if __name__ == "__main__":
    main()
