# -*- coding: utf-8 -*-
r"""cluster_stories.py - 同类方案调研 P0-3：事件聚类（学 Meridian/ClueArk，取轻量版）
问题：本项目只有"去重"没有"聚类"——同一事件被 10 家媒体报道，会以 10 条独立卡片进
仪表盘，假设 evidence_log 也记 10 条重复证据，稀释 ACH 矩阵。
方案：纯标准库 TF-IDF（中文按字符 2-gram、英文按词）+ 余弦相似度 + 并查集连通分量。
**不引入 sklearn**（保持 6 依赖极轻量哲学，数千条规模纯 Python 秒级）。
跨语言：优先用 cn_title/cn_summary（CI 已翻译），让中文源和外国源落在同一语义空间。
供 refresh.rebuild_data() / link_intel_hyp.main() 调用 assign_story_ids(items)；
__main__ 可对全量 jsonl 做统计与人工抽查。
"""
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\osint\data")
SIM_THRESHOLD = 0.65     # 余弦相似度阈值：>= 归为同一事件（实测 0.55 会把财报模板句串簇）
SUMMARY_GATE = 0.30      # 摘要闸门：两边都有摘要时，摘要相似度须达标才合并
                         # （真事件标题+摘要双高相似；财报模板句/每日公告摘要摘要各说各话）
STORY_TIME_GATE = 48 * 3600   # 时间闸门：候选对发布时间差超过 48h 不合并
                         # （同源每日摘要/社论的模板标题靠文本分不开，但跨天是不同事件）
WINDOW_DAYS = 14         # 只对最近 N 天的条目聚类（更旧的不写 story_id）
SIG_TOKENS = 12          # 每条取权重最高的 N 个 token 做"签名"，控制候选对规模
MAX_POSTING = 200        # 签名倒排的 posting 上限（太泛的 token 跳过，防对数爆炸）
MAX_DF_RATIO = 0.3       # df/n 超过此比例的 token 视为"停用词级"，不参与相似度

_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
_LAT_WORD = re.compile(r"[a-zA-Z]{2,}")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _tokens(text):
    """中文按字符 2-gram + 英文按小写词 + 数字串"""
    if not text:
        return []
    text = str(text)
    toks = []
    chars = _CJK_CHAR.findall(text)
    for i in range(len(chars) - 1):
        toks.append(chars[i] + chars[i + 1])
    toks.extend(w.lower() for w in _LAT_WORD.findall(text))
    toks.extend(_NUM.findall(text))
    return toks


def _item_text(it):
    """中文优先（翻译过），英文源回退原标题"""
    parts = [it.get("cn_title") or it.get("title") or "",
             it.get("cn_summary") or it.get("summary") or ""]
    return " ".join(p for p in parts if p)


def _title_text(it):
    return str(it.get("cn_title") or it.get("title") or "")


def _sum_text(it):
    return str(it.get("cn_summary") or it.get("summary") or "")


def _parse_ts(s):
    m = _TS_RE.search(str(s or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        int(m.group(4)), int(m.group(5))).timestamp()
    except ValueError:
        return None


def assign_story_ids(items, days=WINDOW_DAYS, threshold=SIM_THRESHOLD,
                     time_gate=STORY_TIME_GATE, summary_gate=SUMMARY_GATE):
    """给条目（in-memory dict 列表）写 story_id / story_size，返回聚类统计。
    只处理 published_at 在最近 days 天内的条目；纯本地计算，失败由调用方兜底。"""
    now = time.time()
    cutoff = now - days * 86400
    cand = []
    for idx, it in enumerate(items):
        ts = _parse_ts(it.get("published_at"))
        if ts is None or ts < cutoff or ts > now + 86400:  # 未来脏日期不聚类
            continue
        cand.append((idx, it, ts))
    n = len(cand)
    if n == 0:
        return {"window_items": 0, "stories": 0, "clustered_items": 0, "largest": 0}

    docs = [_tokens(_item_text(it)) for _, it, _ts in cand]
    sum_docs = [_tokens(_sum_text(it)) for _, it, _ts in cand]

    # DF / IDF（df=1 的 token 不可能产生跨条相似，直接剔除）
    df = {}
    for toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    max_df = max(2, int(MAX_DF_RATIO * n))
    idf = {t: math.log(n / c) for t, c in df.items() if 2 <= c <= max_df}

    # tf * idf，L2 归一。合并向量判相似度，摘要向量做闸门（模板标题的判别信息在摘要）
    weights = []
    weights_sum = []

    def _vec(tok_list):
        w = {}
        for t in tok_list:
            i = idf.get(t)
            if i is not None:
                w[t] = w.get(t, 0.0) + i
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        return {t: v / norm for t, v in w.items()}

    for toks, sum_toks in zip(docs, sum_docs):
        weights.append(_vec(toks))
        weights_sum.append(_vec(sum_toks))

    # 签名 token 倒排 → 候选对（近似 MinHash：同事件几乎必然共享高权重 token）
    inv = {}
    for i, w in enumerate(weights):
        for t in sorted(w, key=w.get, reverse=True)[:SIG_TOKENS]:
            inv.setdefault(t, []).append(i)
    cand_pairs = set()
    for postings in inv.values():
        L = len(postings)
        if L < 2 or L > MAX_POSTING:
            continue
        for a in range(L):
            for b in range(a + 1, L):
                cand_pairs.add((postings[a], postings[b]))

    # 并查集
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sim_pairs = 0
    for a, b in cand_pairs:
        if abs(cand[a][2] - cand[b][2]) > time_gate:  # 时间闸门：跨天模板条目不是同一事件
            continue
        wa, wb = weights[a], weights[b]
        if len(wa) > len(wb):
            wa, wb = wb, wa
        dot = sum(v * wb.get(t, 0.0) for t, v in wa.items())
        if dot < threshold:
            continue
        # 同源模板规则：同源条目相差>12h 且文本相似 → 大概率是每日摘要/社论/收盘盘点
        # 等固定栏目，不是同一事件的连续报道（真事件同源复挂通常在数小时内）；
        # 跨源合并不受此规则影响——多源报道同一事件才是聚类的核心场景
        if (cand[a][1].get("source_name") == cand[b][1].get("source_name")
                and abs(cand[a][2] - cand[b][2]) > 43200):
            continue
        # 摘要闸门：两边都有摘要时，摘要也得相似才认同一事件
        wsa, wsb = weights_sum[a], weights_sum[b]
        if wsa and wsb:
            if len(wsa) > len(wsb):
                wsa, wsb = wsb, wsa
            if sum(v * wsb.get(t, 0.0) for t, v in wsa.items()) < summary_gate:
                continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        sim_pairs += 1

    # 连通分量 → story_id（取簇内最小条目 id 哈希，跨轮次尽量稳定）
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    n_stories = clustered = largest = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        member_ids = [str(cand[m][1].get("id") or ("idx:%d" % cand[m][0])) for m in members]
        sid = "st_" + hashlib.sha256(min(member_ids).encode("utf-8")).hexdigest()[:10]
        for m in members:
            it = cand[m][1]
            it["story_id"] = sid
            it["story_size"] = len(members)
        n_stories += 1
        clustered += len(members)
        largest = max(largest, len(members))

    return {"window_items": n, "stories": n_stories, "clustered_items": clustered,
            "largest": largest, "sim_pairs": sim_pairs}


def _main():
    """统计模式：读全量 jsonl 跑聚类，打印 stats + 最大 5 个簇的样例标题（人工抽查用）"""
    files = sorted(BASE.glob("intel_2*.jsonl"))
    items = []
    for f in files:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
    print(f"[CLUSTER] 加载 {len(items)} 条 ({len(files)} 个 jsonl 文件)")
    t0 = time.time()
    stats = assign_story_ids(items)
    cost = time.time() - t0
    print(f"[CLUSTER] {stats} · 耗时 {cost:.1f}s")
    # 抽查最大的 5 个簇
    by_story = {}
    for it in items:
        if it.get("story_id"):
            by_story.setdefault(it["story_id"], []).append(it)
    top = sorted(by_story.values(), key=len, reverse=True)[:5]
    for g in top:
        print(f"\n--- {g[0]['story_id']} ({len(g)} 条) ---")
        for it in g[:6]:
            print("  ·", (it.get("cn_title") or it.get("title") or "")[:60],
                  "|", it.get("source_name", ""))


if __name__ == "__main__":
    _main()
