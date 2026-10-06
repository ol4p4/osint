# -*- coding: utf-8 -*-
r"""intel_gate.py - AI 处理的优先级排序（2026-10-05 建 / 2026-10-06 重设计）

## 问题（原）
筛选层（fetch_rss 的 base_score / refresh 的 final_score）一直在算分，但 AI 采集器
取条目时**按 published_at 排序、完全不看这个分**——打分与消费之间是断开的。

## 第一版做错了什么（重要教训）
首版把 `base_score=0` 当作"内容无关"的证据做**硬过滤**。但 `base_score=0` 实际只等于
"没命中关键词表里的词"——而词表再宽也覆盖不了用户口径（**"要一个上知天文下至地理的
参谋"**）。实测扩表后仍有 1396/3761 条得 0 分，其中包含 NASA/SpaceX 乘组撤离、
特朗普动态这类真要闻。**把"没命中我的词表"当成"不值得知道"，是把自己的工具局限
当成了世界的边界。**硬过滤已撤销。

## 现在的设计：排序，不丢弃
- `priority_sort(item)` → 按 (base_score 降序, published_at 降序) 排序。
  高相关条目优先占用 AI 额度；低分条目**不丢**，额度有余时照常处理。
- `rank_key()` 是唯一排序入口，供 translate_local / citizen_impact 共用
  （共享而非各写一份：AGENTS.md 记过同逻辑多副本修一处漏一处的教训）。

**为什么用 base_score 而非 final_score**：
  - base_score = 源权重 × 关键词加权，写入 jsonl 时持久化，实测零缺失；
  - final_score 含时间衰减，且本地 fetch_now/GDELT 条目根本没写这个字段
    （refresh.rebuild 只在内存里补），按它排序会把本地条目全判成 0。

**字段缺失/非法一律当 0 处理**（排到最后但不丢弃）——「不知道相关性」不该等于
「不重要」，但也不该占用高优先位。

环境开关：OSINT_AI_SCORE_GATE=0 时退化为纯时间序（完全等价旧行为，应急回退用）。
"""
import os
from datetime import datetime, timedelta, timezone

# 新鲜度窗口（小时）：窗口内的条目优先于窗口外，防止陈年高分条目霸占队列。
# 实测未翻译池里 3 天以上旧货有 6.4 万条、近 3 天仅 1399 条——若纯按分数排序，
# 旧高分条目会永久占据 AI 额度、饿死新新闻。故采用「两级排序」：先新鲜度，再分数。
RECENCY_WINDOW_H = 72


def gate_enabled():
    """环境开关：OSINT_AI_SCORE_GATE=0/false/no 时关闭按分排序（退化为纯时间序）。"""
    v = os.environ.get("OSINT_AI_SCORE_GATE", "").strip().lower()
    return v not in ("0", "false", "no", "off")


def _score(item):
    try:
        return float(item.get("base_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_recent(item, now):
    """条目是否落在新鲜度窗口内。时间戳无法解析时视为不新鲜（排后但不丢）。"""
    ts = str(item.get("published_at") or "")
    if not ts:
        return False
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts[:len(datetime.now().strftime(fmt))], fmt)
            return (now - dt).total_seconds() <= RECENCY_WINDOW_H * 3600
        except ValueError:
            continue
    return False


def rank_key(item, now=None):
    """排序键，降序使用：(新鲜度层级, base_score, published_at)。

    - 第 1 位保证新鲜条目永远优先（层级 1=新鲜 / 0=陈旧），防止旧高分霸榜；
    - 第 2 位是相关性分（高分先处理）；
    - 第 3 位同分取更新的。
    闸门关闭时退化为纯时间序（旧行为）。
    """
    ts = str(item.get("published_at") or "")
    if not gate_enabled():
        return (0, 0.0, ts)
    now = now or datetime.now()
    return (1 if _is_recent(item, now) else 0, _score(item), ts)


def select_priority(items, max_n, now=None):
    """从候选里挑出优先处理的 max_n 条（不丢弃候选本身）。

    排序 = 新鲜度优先 → 相关性降序 → 时间降序。取前 max_n。
    """
    now = now or datetime.now()
    return sorted(items, key=lambda it: rank_key(it, now), reverse=True)[:max_n]


def priority_sort(items, reverse=True, now=None):
    """全量按优先级排序（默认降序=最该处理的在前）。不丢弃任何条目。"""
    now = now or datetime.now()
    return sorted(items, key=lambda it: rank_key(it, now), reverse=reverse)


# ── 去重（2026-10-06 新增）─────────────────────────────────────────
# 动机：同一事件常被多个源采到 → 标题几乎相同（实测 jaccard 1.00 / 0.83），
# 却因 id=md5(源:链接:标题) 不同而全部保留 → AI 对同一件事分析多遍。
# 实测近 3 天未翻译池 1461 条里 14~17% 是这类跨源重复。
#
# **为什么放在 AI 层而不是采集层**：采集层的 simhash 去重
# （cloud/clean_dedup_score.dedup_items）只在 CI 跑，本地 fetch_now/fetch_gdelt
# 采的条目从不过那道；rebuild 又只按 id 去重（跨源同事件 id 不同，拦不住）。
#
# **不重写去重算法**：直接复用生产的 SimHashDedup + TitleDedup
# （cloud/clean_dedup_score），保证与 CI 侧判定一致，避免两套阈值漂移。
#
# **先去重还是先排序**：先排序（高分+新鲜在前）再去重 → 留下的代表是
# 「最优先的那条」，而不是随机留一条。同时只需从高优先区往下走到够 N 条即可停，
# 不必对 6.5 万条全池做去重（实测 8500 条约 10s，全池会到 ~80s）。

_DEDUP_CACHE = {}


def _headline_key(title):
    """标题归一化键：取【】包围的栏目标题（无【】则整条），再去标点/空格。

    为什么需要这一层：生产 SimHashDedup/TitleDedup 对「【长标题】正文…」vs
    「长标题」（纯标题版）判不出重复——短标题是长标题的子串，jaccard 被长标题
    撑到约 0.38（< 0.7 阈值），simhash 因长度差太大也超距。实测这类"栏目标题版
    + 纯标题版"是同一条新闻的两个来源，占池子约 14%。用 headline 归一化做
    精确/前缀匹配补齐，抽验 210 组全是同事件（多源重复），无误伤。
    """
    import re
    m = re.match(r"^\s*【([^】]+)】", title or "")
    core = m.group(1) if m else (title or "")
    core = re.sub(r"金十数据\d+月\d+日讯[，,]?", "", core)
    return re.sub(r"[\s\W_]+", "", core)


def make_deduper():
    """构造去重器：生产的 SimHash+Title 原语 + headline 归一化补漏。

    返回 add(item) -> bool（True=保留为新代表）。导入失败返回 None。
    """
    try:
        import sys
        from pathlib import Path
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from cloud.clean_dedup_score import SimHashDedup, TitleDedup
    except Exception:
        return None
    sd = SimHashDedup(3)      # 与 CI 侧默认阈值一致（汉明距离 <=3）
    td = TitleDedup(0.7)      # 与 CI 侧默认一致（标题 jaccard >=0.7）
    seen_headlines = set()

    def _add(item):
        iid = str(item.get("id") or "")
        title = str(item.get("title") or "")
        text = title + " " + str(item.get("content_preview") or item.get("content") or "")[:200]
        # 先过 headline 归一化（补 simhash/jaccard 的短板：栏目标题版 vs 纯标题版）
        hk = _headline_key(title)
        if hk:
            if hk in seen_headlines:
                return False
            seen_headlines.add(hk)
        if not sd.add(text, iid):
            return False
        if not td.add(title, iid):
            return False
        return True
    return _add


def dedup_enabled():
    """环境开关：OSINT_AI_DEDUP=0/false/no 时关闭 AI 层去重。"""
    v = os.environ.get("OSINT_AI_DEDUP", "").strip().lower()
    return v not in ("0", "false", "no", "off")


def select_priority_unique(items, max_n, now=None):
    """按优先级排序 + 去重后取前 max_n 条。

    流程：全量排序（新鲜→分数→时间）→ 从上往下走，去过重的条目入选，
    够 max_n 条即停。**返回的条目本身仍完整保留在候选池**（调用方拿到的
    是"本轮该处理谁"，不是"删掉其余"）。

    去重关闭（OSINT_AI_DEDUP=0）或原语导入失败时，退化为纯 select_priority。
    """
    now = now or datetime.now()
    ordered = sorted(items, key=lambda it: rank_key(it, now), reverse=True)
    if not dedup_enabled():
        return ordered[:max_n]
    add = make_deduper()
    if add is None:
        return ordered[:max_n]
    picked = []
    for it in ordered:
        if add(it):
            picked.append(it)
            if len(picked) >= max_n:
                break
    return picked

