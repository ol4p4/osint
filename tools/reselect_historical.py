# -*- coding: utf-8 -*-
r"""reselect_historical.py - 用新排序键重选历史窗口的证据（2026-09-30）

**问题**：排序键 2026-09-30 从 `(tfidf优先, -relevance, -base_score)` 换成
`(tfidf优先, -(kw_hits*10 + body_len/100))`，但**只影响新灌入的证据**。
cap=6 生效期间（2026-09-14 ~ 09-28）的历史证据仍是旧键选出的，而旧键的
AUC=0.4762（低于随机）——等于从候选池里随机抽签。

**本工具**：
  1. 逐日重建候选池（复用生产匹配：TF-IDF → DOMAIN 兜底，3 天窗口 + story 去重）
  2. 用**新键**重选 top-N
  3. 对比现有 evidence_log：重叠 / 新增 / 新增的质量代理指标
  4. `--write` 时**补充**新增条目（保留旧的——它们已被 JEV 诊断，删除等于丢掉已付成本）

**为什么是补充而非替换**：旧条目已进 ACH 矩阵（钱已花），删除不省任何成本；
补充能把旧键漏掉的真信号捞回来。

用法：
    python tools/reselect_historical.py --dry
    python tools/reselect_historical.py --write
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs
from link_intel_hyp import (
    DOMAIN_MAP, DOMAIN_MIN_SCORE, EVIDENCE_DAILY_CAP,
    build_tfidf_vectors, match_intel_tfidf, match_intel_to_hyp, _ach_eligible,
)

_intel_cache = {}


def load_day(day_str):
    """day_str: 'YYYYMMDD' → 条目列表（带缓存，避免重复读盘）"""
    if day_str in _intel_cache:
        return _intel_cache[day_str]
    p = BASE / f"intel_{day_str}.jsonl"
    items = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    _intel_cache[day_str] = items
    return items


def load_window(end_date, days=3):
    """以 end_date 结尾的 N 天窗口（与 link_intel_hyp 读取窗口一致）"""
    d0 = datetime.strptime(end_date, "%Y-%m-%d")
    seen, out = set(), []
    for i in range(days):
        day = (d0 - timedelta(days=i)).strftime("%Y%m%d")
        for it in load_day(day):
            iid = it.get("id")
            if iid and iid in seen:
                continue
            if iid:
                seen.add(iid)
            out.append(it)
    return out


def hyp_doc(h):
    """与 link_intel_hyp 的假设文档构成完全一致"""
    ind = " ".join(str(i.get("name", "")) for i in (h.get("indicators") or [])
                   if isinstance(i, dict))
    return " ".join([str(h.get("title", "")), str(h.get("core_claim") or ""),
                     str(h.get("rationale") or ""),
                     str(h.get("falsification_criteria") or "")[:200], ind])


def sel_key(intel, match):
    """与生产 link_intel_hyp._sel_key 完全一致（新键）"""
    try:
        kw = len(intel.get("keywords_hit") or [])
    except (TypeError, ValueError):
        kw = 0
    try:
        blen = len(str(intel.get("cn_summary") or intel.get("content_preview")
                        or intel.get("content") or ""))
    except (TypeError, ValueError):
        blen = 0
    return (0 if match.get("method") == "tfidf" else 1,
            -(kw * 10 + blen / 100.0))


def build_kw_idf(intel_items):
    n_docs = len(intel_items) or 1
    all_kws = {kw.lower() for kws in DOMAIN_MAP.values() for kw in kws}
    df = {kw: 0 for kw in all_kws}
    for it in intel_items:
        txt = cs._item_text(it).lower()
        for kw in all_kws:
            if kw in txt:
                df[kw] += 1
    return {kw: math.log(n_docs / (1.0 + max(c, 1))) for kw, c in df.items()}


def build_pool(intel_items, hyps, kw_idf):
    """重建候选池 → [(hyp_id, intel, match)]，与生产 Pass 1 同构"""
    hyp_docs = [cs._tokens(hyp_doc(h)) for h in hyps]
    intel_docs = [cs._tokens(cs._item_text(it)) for it in intel_items]
    all_vecs = build_tfidf_vectors(hyp_docs + intel_docs)
    hyp_vecs = all_vecs[:len(hyps)]
    intel_vecs = all_vecs[len(hyps):]

    pool = []
    for idx, intel in enumerate(intel_items):
        matches = match_intel_tfidf(intel_vecs[idx], hyp_vecs, hyps)
        if not matches:
            matches = match_intel_to_hyp(intel, hyps, min_score=DOMAIN_MIN_SCORE,
                                         kw_idf=kw_idf)
        for m in matches[:2]:   # 与生产一致：每条最多取 2 个假设
            pool.append((m["hyp_id"], intel, m))
    return pool


def make_entry(intel, match, date_str):
    """构造 evidence 条目（与 update_hyp_evidence 的 schema 一致，但用历史日期）"""
    return {
        "date": date_str,
        "intel_ids": [intel.get("id", "")],
        "story_ids": [intel["story_id"]] if intel.get("story_id") else [],
        "summary": (intel.get("cn_title", "") or intel.get("title", ""))[:100],
        "body": (intel.get("cn_summary") or intel.get("content_preview")
                 or intel.get("content") or "")[:400],
        "domains": match.get("domains", []),
        "relevance": match.get("relevance_score", 0),
        "ach_eligible": _ach_eligible(match),
        "source": intel.get("source_name", ""),
        "impact": intel.get("impact", "")[:200],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-09-14")
    ap.add_argument("--to", dest="date_to", default="2026-09-28")
    ap.add_argument("--cap", type=int, default=EVIDENCE_DAILY_CAP,
                    help=f"每假设每日上限（默认生产值 {EVIDENCE_DAILY_CAP}）")
    ap.add_argument("--dry", action="store_true", help="只报告，不写盘")
    ap.add_argument("--write", action="store_true", help="实际补充新增条目")
    args = ap.parse_args()
    if not args.dry and not args.write:
        print("请指定 --dry 或 --write")
        return 1

    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]

    # 现有条目索引（**跨日期**去重——同一 intel 可能在别的日期已入库）
    have_intel = defaultdict(set)   # hyp_id -> {intel_id}
    have_story = defaultdict(set)   # hyp_id -> {story_id}
    dated_count = defaultdict(int)
    for h in majors:
        for e in (h.get("evidence_log") or []):
            for iid in (e.get("intel_ids") or []):
                have_intel[h["id"]].add(iid)
            for sid in (e.get("story_ids") or []):
                have_story[h["id"]].add(sid)
            dated_count[str(e.get("date"))[:10]] += 1

    d0 = datetime.strptime(args.date_from, "%Y-%m-%d")
    d1 = datetime.strptime(args.date_to, "%Y-%m-%d")
    dates, cur = [], d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    print(f"重选窗口: {args.date_from} ~ {args.date_to}（{len(dates)} 天）| cap={args.cap}")
    print(f"major 假设 {len(majors)} 个 | 现有证据 {sum(dated_count.values())} 条")
    print("=" * 80)
    print(f"{'日期':<12}{'池':>7}{'现有':>6}{'新选':>6}{'重叠':>6}{'新增':>6}"
          f"{'新增kw均值':>11}{'新增blen均值':>13}")

    total_add = 0
    additions = defaultdict(list)   # hyp_id -> [entry]
    for D in dates:
        items = load_window(D, days=3)
        if not items:
            print(f"{D:<12}  (无 intel 数据，跳过)")
            continue
        cs.assign_story_ids(items)
        kw_idf = build_kw_idf(items)
        pool = build_pool(items, majors, kw_idf)

        by_hyp = defaultdict(list)
        for hid, intel, m in pool:
            by_hyp[hid].append((intel, m))

        picked, new_add = [], []
        for hid, cands in by_hyp.items():
            cands.sort(key=lambda c: sel_key(c[0], c[1]))
            for intel, m in cands[:args.cap]:
                picked.append((hid, intel, m))
                iid = intel.get("id")
                sid = intel.get("story_id")
                if iid and iid in have_intel[hid]:
                    continue
                if sid and sid in have_story[hid]:
                    continue
                new_add.append((hid, intel, m))

        kws = [len(it.get("keywords_hit") or []) for _h, it, _m in new_add]
        blens = [len(str(it.get("cn_summary") or it.get("content_preview")
                         or it.get("content") or "")) for _h, it, _m in new_add]
        kw_avg = sum(kws) / len(kws) if kws else 0
        bl_avg = sum(blens) / len(blens) if blens else 0
        print(f"{D:<12}{len(pool):>7}{dated_count.get(D, 0):>6}{len(picked):>6}"
              f"{len(picked) - len(new_add):>6}{len(new_add):>6}"
              f"{kw_avg:>11.2f}{bl_avg:>13.0f}")
        total_add += len(new_add)
        for hid, intel, m in new_add:
            additions[hid].append(make_entry(intel, m, D))

    print("=" * 80)
    print(f"合计新增 {total_add} 条（不删旧的——已诊断的删除等于丢掉已付成本）")
    est = total_add * 1500 * 0.042 / 1e6
    print(f"预计诊断成本: ${est:.4f}（{total_add} 条 × ~1500 token × $0.042/Mtok）")
    if args.cap != 6:
        print(f"\n注：cap={args.cap} 是当前生产值（旧窗口当时是 6）——"
              f"这里按当前标准重选，因此新增量含 cap 提升的贡献。")

    if args.write:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = HYP_FILE.with_suffix(f".json.bak_reselect_{ts}")
        bak.write_text(HYP_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        for h in majors:
            if h["id"] in additions:
                h.setdefault("evidence_log", []).extend(additions[h["id"]])
        HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已补充 {total_add} 条（备份 {bak.name}）")
        print("下一步: python tools/ach_daily_batch.py --force  # 诊断新条目")
    else:
        print("\n（--dry 模式，未写盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
