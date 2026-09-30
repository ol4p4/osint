# -*- coding: utf-8 -*-
r"""verify_cluster_regression.py - 分词器切换对事件聚类的回归验证（2026-09-30，只读）

**风险**：`SIM_THRESHOLD=0.65` 是在字符 2-gram 空间校准的。分词器换成 SP-unigram
后 token 粒度变粗，余弦分布会移动——**同一阈值在新空间下含义不同**，可能
导致聚类过松（误聚）或过紧（该聚的不聚）。

**方法**：同一批真实语料，用两套分词器各跑一次 `assign_story_ids`，对比：
  - 簇数量 / 归簇条目数 / 最大簇规模（宏观是否失控）
  - 大簇内容人工抽查（是否有明显误聚）
  - 同源模板句是否被正确隔离

**只读，不写盘**（用副本跑，不污染真实数据）。

用法：
    python tools/verify_cluster_regression.py
"""
import copy
import glob
import json
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs


def load_items(days=3):
    files = sorted(glob.glob(str(BASE / "intel_2026*.jsonl")), reverse=True)[:days + 2]
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


def run_cluster(items, use_sp):
    """用指定分词器跑聚类（在副本上，不动原数据）"""
    orig_tried, orig_proc = cs._SP_TRIED, cs._SP_PROCESSOR
    if use_sp:
        cs._SP_TRIED, cs._SP_PROCESSOR = False, None   # 触发重新加载
        cs._get_sp()
    else:
        cs._SP_TRIED, cs._SP_PROCESSOR = True, None    # 强制降级
    try:
        data = copy.deepcopy(items)
        stats = cs.assign_story_ids(data)
        # 收集簇
        clusters = {}
        for it in data:
            sid = it.get("story_id")
            if sid:
                clusters.setdefault(sid, []).append(it)
        return stats, clusters, data
    finally:
        cs._SP_TRIED, cs._SP_PROCESSOR = orig_tried, orig_proc


def summarize(tag, stats, clusters):
    sizes = sorted((len(v) for v in clusters.values()), reverse=True)
    print(f"\n[{tag}]")
    print(f"  窗口条目 {stats.get('window_items')} | 簇 {stats.get('stories')} | "
          f"归簇条目 {stats.get('clustered_items')} | 最大簇 {stats.get('largest')}")
    multi = [s for s in sizes if s >= 2]
    print(f"  多条目簇 {len(multi)} 个，规模分布 top10: {multi[:10]}")
    return sizes


def main():
    items = load_items(3)
    print(f"语料 {len(items)} 条（去重后）")

    stats_2g, cl_2g, _ = run_cluster(items, use_sp=False)
    stats_sp, cl_sp, data_sp = run_cluster(items, use_sp=True)

    s2 = summarize("字符 2-gram（升级前）", stats_2g, cl_2g)
    ssp = summarize("SP-unigram（生产代码）", stats_sp, cl_sp)

    # ---- 宏观对比 ----
    print("\n" + "=" * 62)
    print("宏观对比:")
    w = stats_sp.get("window_items") or 1
    print(f"  归簇率: 2-gram {100*stats_2g.get('clustered_items',0)/w:.1f}%  →  "
          f"SP {100*stats_sp.get('clustered_items',0)/w:.1f}%")
    print(f"  簇数:   2-gram {stats_2g.get('stories')}  →  SP {stats_sp.get('stories')}")
    print(f"  最大簇: 2-gram {stats_2g.get('largest')}  →  SP {stats_sp.get('largest')}")

    # ---- 抽查最大簇（误聚最可能暴露的地方）----
    print("\n" + "=" * 62)
    print("最大簇内容抽查（SP 空间，规模 >=3）:")
    big = sorted(cl_sp.items(), key=lambda kv: -len(kv[1]))[:4]
    for sid, members in big:
        if len(members) < 3:
            continue
        print(f"\n  簇 {sid} ({len(members)} 条):")
        for m in members[:6]:
            src = m.get("source") or m.get("source_name") or "?"
            ttl = str(m.get("cn_title") or m.get("title") or "")[:58]
            print(f"    [{str(src)[:10]:<10}] {ttl}")

    # ---- 同源模板句检查（固定栏目不应互相聚）----
    print("\n" + "=" * 62)
    print("同源模板句检查（同一 source 内 >=3 条同簇 = 可疑）:")
    suspicious = 0
    for sid, members in cl_sp.items():
        if len(members) < 3:
            continue
        by_src = {}
        for m in members:
            by_src.setdefault(str(m.get("source") or m.get("source_name") or "?"), []).append(m)
        for src, ms in by_src.items():
            if len(ms) >= 3:
                suspicious += 1
                print(f"  ⚠ 簇 {sid} 内 {src} 占 {len(ms)}/{len(members)} 条:")
                for m in ms[:3]:
                    print(f"      {str(m.get('cn_title') or m.get('title') or '')[:56]}")
    if not suspicious:
        print("  ✓ 未发现同源模板句成簇")

    # ---- 两套分词器聚类一致性（同事件应被两套都识别）----
    print("\n" + "=" * 62)
    pairs_2g = set()
    for sid, members in cl_2g.items():
        ids = sorted(str(m.get("id")) for m in members)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs_2g.add((ids[i], ids[j]))
    pairs_sp = set()
    for sid, members in cl_sp.items():
        ids = sorted(str(m.get("id")) for m in members)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs_sp.add((ids[i], ids[j]))
    inter = pairs_2g & pairs_sp
    print(f"  2-gram 同簇对 {len(pairs_2g)} | SP 同簇对 {len(pairs_sp)} | 交集 {len(inter)}")
    if pairs_2g:
        print(f"  SP 保留了 2-gram 判定的 {100*len(inter)/len(pairs_2g):.1f}%")
    only_sp = pairs_sp - pairs_2g
    print(f"  SP 新增同簇对 {len(only_sp)}（可能是真实同事件被旧分词漏掉）")
    if only_sp:
        print("  新增样例（抽查是否合理）:")
        for a, b in list(only_sp)[:5]:
            ta = tb = ""
            for m in data_sp:
                if str(m.get("id")) == a:
                    ta = str(m.get("cn_title") or m.get("title") or "")[:44]
                if str(m.get("id")) == b:
                    tb = str(m.get("cn_title") or m.get("title") or "")[:44]
            print(f"    A: {ta}")
            print(f"    B: {tb}")
            print()


if __name__ == "__main__":
    main()
