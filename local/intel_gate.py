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

