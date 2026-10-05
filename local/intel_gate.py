# -*- coding: utf-8 -*-
r"""intel_gate.py - AI 处理的准入闸门（2026-10-05）

背景：筛选层（fetch_rss 的 base_score / refresh 的 final_score）一直在算分，
但 AI 采集器取条目时**按 published_at 排序、完全不看这个分**——实测近 5 天
54%~68% 的已翻译/已研判条目是 base_score=0（关键词一个都没命中，如"银行金条
价格排行""EUR/USD fell 0.38%"这类行情播报）。等于三分之二的 AI 预算花在
筛选认为无关的条目上。

本模块给"是否值得送 AI"一个统一判据，供 translate_local / citizen_impact 共用。
共享而非各写一份，是因为 AGENTS.md 记过同类教训：同逻辑多副本时修一处漏一处，
导致静默退化（translate_local 修了 max_tokens、citizen_impact 漏改那一次）。

**判据用 base_score，不用 final_score**：
  - base_score 是内容相关性（源权重 × 关键词加权），写入 jsonl 时持久化，实测零缺失；
  - final_score 含时间衰减，且本地 fetch_now/GDELT 条目根本没写这个字段
    （refresh.rebuild 只在内存里补），按它过滤会把全部本地条目误杀。

**阈值取 base_score > 0**：实测 base_score==0 与 keywords_hit 为空完全等价
（今日 0 条例外），零误杀；近 3 天日均保留 422 条，落在现有配额内。
正分条目下界 0.077（GDELT 保底 0.3），与 0 之间有充足间隔。

**宁可缺不可错**：字段缺失时**放行**（无法判断→不丢弃），只有明确等于 0 才拦。
批量过滤的准则是"没有证据说明它无关"，而不是"没有证据说明它有关"。

环境开关：OSINT_AI_SCORE_GATE=0 关闭闸门（全量处理，应急/回填用）。
"""
import os

# 闸门阈值：base_score 严格大于此值才准入
MIN_BASE_SCORE = 0.0


def gate_enabled():
    """环境开关：OSINT_AI_SCORE_GATE=0/false/no 时关闭（默认开启）。"""
    v = os.environ.get("OSINT_AI_SCORE_GATE", "").strip().lower()
    return v not in ("0", "false", "no", "off")


def relevance_ok(item):
    """该条目是否达到送 AI 的相关性门槛。

    - base_score 缺失 → True（无法判断，不丢弃）
    - base_score > 0  → True
    - base_score == 0 → False（筛选层明确判其与 persona 主题无关）
    """
    if not isinstance(item, dict):
        return True
    if "base_score" not in item:
        return True
    try:
        return float(item.get("base_score") or 0) > MIN_BASE_SCORE
    except (TypeError, ValueError):
        return True


def filter_relevant(items):
    """按闸门过滤；返回 (保留列表, 被拦数量)。闸门关闭时原样返回。"""
    if not gate_enabled():
        return list(items), 0
    kept = [it for it in items if relevance_ok(it)]
    return kept, len(items) - len(kept)
