#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性脚本：把历史鼠疫/公卫情报回填到 HM004 假设（跑一次即弃）。

## 动机（2026-10-09）

HM004 建于本轮改造，但 `link_intel_hyp.py` 只处理**近期窗口**的情报，不会
回头扫 10-02~10-09 已入库的 51 条鼠疫相关条目。不补这一步，新假设的
evidence_log 为空，ACH 无法诊断，节点等于白建。

## 关键设计

1. **复用生产函数**：调用 `link_intel_hyp.update_hyp_evidence()` 而非自己拼
   schema——保证字段（body/ach_eligible/story_ids 等）与在线路径逐字一致，
   避免"回填条目与新增条目结构不同"导致的下游分支。
2. **匹配用 DOMAIN 而非 TF-IDF**：历史条目没有 story_id，且 TF-IDF 需要
   全库语料重算；DOMAIN 兜底（本轮新增「公共卫生」域）已足够精确，
   且 `match_intel_to_hyp` 会给出 relevance_score 供 ach_eligible 判定。
3. **去重靠 update_hyp_evidence 自带的幂等**：它按 intel_id / story_id 判重，
   重复跑不会灌重复证据。
4. **配额**：受 `EVIDENCE_DAILY_CAP` 约束会截断——回填是历史补偿，故显式
   按 base_score 降序取 Top N（默认 30），避免把 51 条全灌进去挤占后续配额。
   cap 的语义是"每假设每日"，回填的历史条目 date 字段用原始发布日，
   不与今日配额冲突。

用法：
    python tools/backfill_hm004_evidence.py --dry      # 只看将挂载哪些
    python tools/backfill_hm004_evidence.py            # 实际写入
    python tools/backfill_hm004_evidence.py --top 50   # 调上限
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HYP_FILE = ROOT / "data" / "hypotheses" / "active_hypotheses.json"
TARGET_ID = "HM004"

# 公卫关键词（与 sources.yaml 公卫词表 / DOMAIN_MAP 公共卫生域对齐）
PUBLIC_HEALTH_WORDS = [
    "鼠疫", "肺鼠疫", "炭疽", "霍乱", "埃博拉", "不明原因肺炎", "传染病", "公共卫生",
    "生物安全", "检疫", "疾控", "世卫组织", "世界卫生组织", "肺炎", "疫苗",
    "plague", "pneumonic", "anthrax", "cholera", "ebola", "quarantine", "pandemic",
    "infectious disease", "biosecurity", "outbreak", "epidemic", "pneumonia",
    "vaccine", "world health organization", "public health",
]


def load_intel_items(days=10):
    """读近 N 天的 intel 文件，按 id 去重"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    seen = {}
    for fp in sorted((ROOT / "data").glob("intel_2*.jsonl")):
        date = fp.stem.replace("intel_", "")
        if date < cutoff:
            continue
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                iid = d.get("id")
                if iid and iid not in seen:
                    seen[iid] = d
    return list(seen.values())


def is_public_health(item):
    """粗筛：标题+正文含任一公卫词（后续交给 DOMAIN 匹配做精判）"""
    text = ((item.get("cn_title") or "") + " " + (item.get("title") or "") + " "
            + (item.get("content_preview") or item.get("content") or "")[:600]).lower()
    return any(w.lower() in text for w in PUBLIC_HEALTH_WORDS)


# 行情播报特征词（2026-10-09 加）：实测「美股三大指数开盘…Vaxcyte涨超54%」
# 「A股四大指数开盘表现分化，动物疫苗板块走强」这类条目因字面含"疫苗"被
# DOMAIN 匹配进来，但语义是**股市行情**而非公共卫生事件。
# 判据：含行情词 + 公卫词命中数少（≤1）→ 判为行情条，不挂载。
MARKET_WORDS = [
    "涨超", "涨跌不一", "开盘", "收盘", "指数涨", "指数跌", "板块走强", "板块涨",
    "盘中", "涨停", "跌停", "股指", "股价", "收涨", "收跌", "premarket", "shares rose",
    "shares fell", "stocks", "index", "道指", "纳指", "标普",
]


def is_market_noise(item):
    """判断是否「含公卫词但实为行情播报」的噪声条目。

    2026-10-09 二次修正：首版按「公卫词命中数 ≤1」判据失效——Vaxcyte 是
    **肺炎疫苗公司**，其行情稿正文同时含"肺炎"+"疫苗"（ph=2），与真公卫报道
    无法用词数区分。改用**标题判据**：真公卫新闻的标题必含公卫词
    （"俄官方称鼠疫研究机构…"），而行情稿标题只含公司名与涨跌幅
    （"Vaxcyte美股盘前大涨超85%"）——公卫词仅出现在正文背景介绍里。
    """
    title = ((item.get("cn_title") or "") + " " + (item.get("title") or "")).lower()
    body = (item.get("content_preview") or item.get("content") or "")[:600].lower()
    title_ph = sum(1 for w in PUBLIC_HEALTH_WORDS if w.lower() in title)
    if title_ph > 0:
        return False        # 标题含公卫词 → 真公卫报道
    body_ph = sum(1 for w in PUBLIC_HEALTH_WORDS if w.lower() in body)
    market_hits = sum(1 for w in MARKET_WORDS if w.lower() in (title + " " + body))
    # 标题无公卫词 + 带行情词 → 行情条（公卫词只是正文背景）
    return body_ph >= 1 and market_hits >= 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只列出将挂载的条目")
    ap.add_argument("--top", type=int, default=30, help="最多挂载条数（按 base_score 降序）")
    ap.add_argument("--days", type=int, default=10, help="扫描窗口天数")
    args = ap.parse_args()

    import link_intel_hyp as L

    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    target = next((n for n in nodes if n.get("id") == TARGET_ID), None)
    if target is None:
        print(f"[BACKFILL] 目标节点 {TARGET_ID} 不存在，请先跑 add_hm004_biosafety.py")
        return 1
    print(f"[BACKFILL] 目标: {TARGET_ID} {target.get('title')}")
    print(f"[BACKFILL] 当前 evidence_log: {len(target.get('evidence_log') or [])} 条")

    items = load_intel_items(args.days)
    print(f"[BACKFILL] 扫描 {len(items)} 条情报（近 {args.days} 天）")

    cands = [x for x in items if is_public_health(x)]
    print(f"[BACKFILL] 粗筛公卫相关: {len(cands)} 条")
    # 剔行情噪声（含公卫词但实为股市播报）
    noise = [x for x in cands if is_market_noise(x)]
    cands = [x for x in cands if not is_market_noise(x)]
    print(f"[BACKFILL] 剔除行情噪声: {len(noise)} 条，剩 {len(cands)} 条")

    # 排序键用「公卫词命中数」而非 base_score（2026-10-09 踩坑）：
    # 首版按 base_score 降序取 top30，结果"美股三大指数开盘"（命中"疫苗"1 词、
    # 但财经词一堆导致 base_score 高）挤掉了真鼠疫条目。base_score 衡量的是
    # **整体新闻价值**（含财经/地缘词），不是**公卫相关性**。
    def _ph_hits(x):
        text = ((x.get("cn_title") or "") + " " + (x.get("title") or "") + " "
                + (x.get("content_preview") or x.get("content") or "")[:600]).lower()
        return sum(1 for w in PUBLIC_HEALTH_WORDS if w.lower() in text)

    cands.sort(key=lambda x: (_ph_hits(x), x.get("base_score") or 0,
                              x.get("published_at") or ""), reverse=True)
    picked = cands[: args.top]
    print(f"[BACKFILL] 取前 {len(picked)} 条（--top {args.top}，按公卫词命中数排序）")

    # 用生产函数做 DOMAIN 匹配
    existing = {iid for e in (target.get("evidence_log") or [])
                for iid in (e.get("intel_ids") or [])}

    added = 0
    skipped = 0
    plan = []
    for it in picked:
        iid = it.get("id")
        if iid in existing:
            skipped += 1
            continue
        # match_intel_to_hyp 接受假设**列表**，返回匹配列表（按 relevance 降序 top3）
        matches = L.match_intel_to_hyp(it, [target], min_score=L.DOMAIN_MIN_SCORE)
        mi = next((m for m in matches if m.get("hyp_id") == TARGET_ID), None)
        if not mi:
            skipped += 1
            continue
        plan.append((it, mi))

    print(f"[BACKFILL] 通过 DOMAIN 匹配: {len(plan)} 条（跳过 {skipped}）")
    if args.dry:
        for it, mi in plan:
            print(f"   {round(mi.get('relevance_score', 0), 3)} | {it.get('published_at','')[:16]} "
                  f"| {(it.get('cn_title') or it.get('title') or '')[:56]} | {mi.get('domains')}")
        print("[BACKFILL] --dry 模式，未写盘")
        return 0

    # 写入
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = HYP_FILE.with_name(HYP_FILE.name + f".bak_pre_backfill_hm004_{stamp}")
    shutil.copy2(HYP_FILE, bak)
    print(f"[BACKFILL] 已备份 -> {bak.name}")

    for it, mi in plan:
        n = L.update_hyp_evidence(target, it, mi)
        added += n

    tmp = HYP_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(nodes, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HYP_FILE)
    print(f"[BACKFILL] 完成：新增 {added} 条证据，"
          f"{TARGET_ID} evidence_log 共 {len(target.get('evidence_log') or [])} 条")

    # eligible 统计
    elig = sum(1 for e in (target.get("evidence_log") or []) if e.get("ach_eligible"))
    print(f"[BACKFILL] 其中 ACH eligible: {elig} 条（进入诊断队列）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
