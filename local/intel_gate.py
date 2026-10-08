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


# ── 固定栏目条识别（2026-10-06）────────────────────────────────────
# 什么是"固定栏目条"：金十/华尔街见闻等源的模板化栏目，**一条标题打包多则新闻**，
# 如「金十数据整理：中东局势跟踪（10月3日）」正文里 18 个编号项、
# 「昨日今晨重要新闻汇总」11 项、「<新闻联播>要闻19条」19 项。
#
# **为什么不删除**：实测栏目条与独立条目只有 **16% 内容重叠**（token 覆盖率 >=0.6
# 判定）——它不是重复，删了就真丢信息。栏目本身是"入口"，值得让人读到。
#
# **为什么要降权**：一条里塞 10~18 个不同主题，AI 做"对毕业生的四维影响分析"
# 必然错位。实测研判结果对比：
#   栏目条 →「多新闻汇总影响公民物价如能源价格、安全如地缘政治、资产如科技股市，
#            政策需应对国际变量，综合传导显著」（18 件事挤成一句空话）
#   单条   →「资管产品总规模突破 88 万亿，彰显金融市场活力，影响居民财富管理」（具体）
# 而这类条目因**关键词命中数爆表**（一条含十几个主题词）分数异常高（实测 0.97/0.94），
# 会挤占真·单条新闻的 AI 额度。故降到同新鲜度层级内的末位：**不丢，但不抢额**。
_COLUMN_PATTERNS = (
    "金十数据整理", "金十整理", "要闻速递", "重要新闻汇总", "消息汇总", "新闻汇总",
    "局势跟踪", "隔夜要闻一览", "要闻一览", "新闻联播", "财经早餐", "见闻早餐",
    "盘前要闻", "盘后要闻", "早报", "晚报", "午报", "周报汇总", "市场要闻回顾",
)


def is_column_item(item):
    """是否固定栏目条（多主题打包）。判据=标题含栏目模板词。"""
    title = str(item.get("title") or "")
    if not title:
        return False
    return any(p in title for p in _COLUMN_PATTERNS)


def rank_key(item, now=None):
    """排序键，降序使用：(新鲜度层级, 非栏目, base_score, published_at)。

    - 第 1 位保证新鲜条目永远优先（层级 1=新鲜 / 0=陈旧），防止旧高分霸榜；
    - 第 2 位把固定栏目条排到同层级末位（不丢，但不抢 AI 额度，见上方说明）；
    - 第 3 位是相关性分（高分先处理）；
    - 第 4 位同分取更新的。
    闸门关闭时退化为纯时间序（旧行为）。
    """
    ts = str(item.get("published_at") or "")
    if not gate_enabled():
        return (0, 1, 0.0, ts)
    now = now or datetime.now()
    return (1 if _is_recent(item, now) else 0,
            0 if is_column_item(item) else 1,
            _score(item),
            ts)


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


# ── 外语源保底带（2026-10-08）──────────────────────────────────────
# 为什么需要：关键词表以中文为主（词表 153 词里中文 90 个，且都偏
# 「就业/能源/宏观」），外语条目因此系统性低分——实测未研判池 en 13482 条
# 的 base_score 中位是 **0.000**，而 cn 条目 100% 有 cn_title（外语仅 2%）、
# 分数普遍 0.7+。结果 top240 里 en 只占 1 席。而用户口径是「上知天文下至
# 地理的参谋」，国际侧（路透/CNBC/DW/日经）正是"上知天文"的部分。
#
# 与 §"AI 准入排序"的硬过滤教训同源：**低分不等于不重要，可能只是我的词表
# 没覆盖**。所以这里只做"保底配额"（不丢任何条目、不改排序），不是过滤。
#
# 配比 25%：留够国际视角，又不至于挤掉中文主战场（国内源是 persona 直接
# 相关）。环境变量 OSINT_FOREIGN_RESERVE=0 可关闭，=0.4 可调高。
FOREIGN_RESERVE_RATIO = 0.25
# 哪些算"外语"：显式 language 字段（fetch_rss 用 _detect_lang 标注）为准。
# 缺字段的按中文处理（本地源以中文为主，避免把缺字段条目误判成外语）。
FOREIGN_LANGS = ("en", "eng", "ja", "jp", "ko", "kr", "fr", "de", "es", "ru", "ar")


def is_foreign(item):
    """是否外语条目（用于保底带配额计数）。"""
    lang = str(item.get("language") or "").strip().lower()
    return lang in FOREIGN_LANGS


def _foreign_quota_enabled():
    v = os.environ.get("OSINT_FOREIGN_RESERVE", "").strip().lower()
    return v not in ("0", "false", "no", "off")


def select_priority_unique(items, max_n, now=None):
    """按优先级排序 + 去重后取前 max_n 条。

    流程：全量排序（新鲜→分数→时间）→ 从上往下走，去过重的条目入选，
    够 max_n 条即停。**返回的条目本身仍完整保留在候选池**（调用方拿到的
    是"本轮该处理谁"，不是"删掉其余"）。

    去重关闭（OSINT_AI_DEDUP=0）或原语导入失败时，退化为纯 select_priority。

    **外语源保底带（2026-10-08 新增，默认 25%）**：纯排序下外语条目系统性
    饿死——实测未研判池 6.8 万条里 `en` 有 13482 条，但 base_score 中位为
    0.000（关键词表以中文为主），且仅 2% 有 cn_title（中文源才刚由本地
    摘标题补上），导致 top240 里 en 只占 1 席、排在第 218 位之后。
    保底带按「分数降序」在**全部未入选条目里**补足外语席位，让外语源
    不因词表偏向而永久排不上队。配比灵感来自 refresh.rebuild_data 的
    RESERVE_TOPICS 主题保底带（同类问题、同类解法）。
    """
    now = now or datetime.now()
    ordered = sorted(items, key=lambda it: rank_key(it, now), reverse=True)
    if not dedup_enabled():
        picked = ordered[:max_n]
    else:
        add = make_deduper()
        if add is None:
            picked = ordered[:max_n]
        else:
            picked = []
            for it in ordered:
                if add(it):
                    picked.append(it)
                    if len(picked) >= max_n:
                        break

    # 外语保底带：见 docstring。只在确实有外语条目被挤出时才生效。
    # **总量严格守恒 = max_n**：首版实现直接 extend，实测 max=50 返回了 59 条
    # （超发会让调用方的预算失控）。改为「从末尾踢掉同数量的非外语条目」。
    try:
        quota = int(max_n * FOREIGN_RESERVE_RATIO)
        if quota > 0 and _foreign_quota_enabled():
            have = sum(1 for x in picked if is_foreign(x))
            need = quota - have
            if need > 0:
                picked_ids = {id(x) for x in picked}
                extra = [x for x in ordered
                         if id(x) not in picked_ids and is_foreign(x)][:need]
                if extra:
                    keep = list(picked)
                    # 只腾出 len(extra) 个位置：从末尾（分数最低处）踢非外语条目。
                    # 踩坑：首版循环条件写错，把中文条目全踢光（上限 50 只剩 18 条）。
                    need_slots = len(extra)
                    idx = len(keep) - 1
                    while need_slots > 0 and idx >= 0:
                        if not is_foreign(keep[idx]):
                            keep.pop(idx)
                            need_slots -= 1
                        idx -= 1
                    picked = keep + extra
                    # 恢复排序语义（下游 translate_local 按顺序分批处理）
                    picked.sort(key=lambda it: rank_key(it, now), reverse=True)
    except Exception:
        pass
    return picked

