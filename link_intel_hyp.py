import json, re, sys, math
from pathlib import Path
from datetime import datetime

HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")
INTEL_DIR = Path(r"D:\osint\data")
OUTPUT_FILE = Path(r"D:\osint\data\link_report.json")

# P1-1（学 RSSidian 轻量版）：情报→假设匹配从 DOMAIN_MAP 关键词表升级为 TF-IDF 余弦相似度，
# 假设(title+core_claim+rationale+证伪判据)与情报(标题/摘要)投到同一向量空间——
# 新假设不再需要手工加关键词。与 tools/cluster_stories.py 共用分词器（中文2-gram+英文词）。
# DOMAIN_MAP 保留作兜底（TF-IDF 无命中时用）。
TFIDF_MIN_SIM = 0.12    # 余弦门槛：短文本相关性经验值
TFIDF_TOP_K = 3

# 2026-09-12 证据准入收紧（实测：DOMAIN 分数无区分度——73% 是 1.0 满分，
# 因为情报与假设常恰好同命中一个域，分数高不代表内容相关）：
#   真正的主闸门 = EVIDENCE_DAILY_CAP（每假设每日限量，分数降序择优）；
#   DOMAIN_MIN_SCORE 只是卫生底线；存量弱证据由 ach_matrix 按 ach_eligible 过滤。
DOMAIN_MIN_SCORE = 0.34      # DOMAIN 兜底相关性下限（域重叠/联合 < 1/3 不记）
# 每假设每日新增证据上限（2026-09-30 由 6 提到 20）。
#
# **为什么提**：cap=6 是为 mimo 决策层设的（实测 45s/条，6 major × 6 = 36/天
# 才能跟上诊断速度）。JEV 接入后单条降到 ~1.0s（两段式两次调用），瓶颈从
# "诊断速度"变成"排序质量"——`tools/backtest_ranking.py` 实测（完整池回测）：
#     cap=6   捕获池中真信号 8%
#     cap=20  捕获 23%（2.9 倍）
# 成本侧完全可承受：6 假设 × (20 直接 + 4 上卷) = 144 条/天，
# 按 JEV 实测 ~1500 token/条、$0.042/Mtok 算 = **$0.0091/天**（$4.35 够用 479 天）。
# 上限仍受 `ach_daily_batch` 的 BATCH=60 约束——超额部分会积压，
# 但 JEV 吞吐已远超该值，故同步观察队列长度（`ach_daily_batch.py --dry`）。
EVIDENCE_DAILY_CAP = 20
ACH_ELIGIBLE_MIN = 0.4       # DOMAIN 兜底证据进入 ACH 诊断队列的分数线（TF-IDF 命中一律 eligible）

# Domain keyword mapping for fuzzy matching
DOMAIN_MAP = {
    "能源": ["能源", "石油", "油价", "原油", "天然气", "煤", "电力", "核电", "新能源", "太阳能", "风电", "储能", "OPEC", "IEA", "oil", "energy", "crude", "gas", "nuclear"],
    "地缘政治": ["战争", "冲突", "制裁", "军事", "外交", "台海", "伊朗", "俄罗斯", "乌克兰", "美国", "北约", "Iran", "Ukraine", "Russia", "war", "sanction", "military", "Taiwan"],
    "经济": ["GDP", "通胀", "CPI", "PPI", "利率", "汇率", "失业", "就业", "消费", "投资", "贸易", "关税", "inflation", "rate", "unemployment", "trade", "tariff"],
    "金融": ["股市", "债券", "基金", "期货", "比特币", "黄金", "银行", "信贷", "M1", "M2", "stock", "bond", "bitcoin", "gold", "bank"],
    "科技": ["AI", "人工智能", "芯片", "半导体", "Nvidia", "算力", "数据中心", "5G", "量子", "chip", "semiconductor", "AI", "data center"],
    "东亚": ["日本", "韩国", "朝鲜", "东亚", "日元", "韩元", "BOJ", "BOK", "Japan", "Korea", "Yen"],
    "社保": ["社保", "养老金", "退休", "养老", "保险", "pension", "retirement", "social security"],
    "青年": ["青年", "失业", "毕业生", "就业", "NEET", "youth", "unemployment", "graduate"],
    "房地产": ["房价", "房地产", "楼市", "房贷", "地产", "housing", "property", "real estate"],
    "供应链": ["供应链", "物流", "运输", "航运", "港口", "supply chain", "shipping", "logistics"],
}

def _load_cluster_module():
    """加载 tools/cluster_stories.py 复用其分词器；失败返回 None（走 DOMAIN_MAP 兜底）"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
        import cluster_stories
        return cluster_stories
    except Exception as e:
        print(f"[TFIDF] cluster_stories 加载失败: {e}")
        return None


def build_tfidf_vectors(docs_tokens):
    """docs_tokens: List[List[str]] → List[dict token->L2 归一 tf-idf 权重]（df<2 剔除）"""
    df = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = len(docs_tokens)
    idf = {t: math.log(n / c) for t, c in df.items() if c >= 2}
    vecs = []
    for toks in docs_tokens:
        w = {}
        for t in toks:
            i = idf.get(t)
            if i is not None:
                w[t] = w.get(t, 0.0) + i
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        vecs.append({t: v / norm for t, v in w.items()})
    return vecs


def match_intel_tfidf(intel_vec, hyp_vecs, hyps, top_k=TFIDF_TOP_K, min_sim=TFIDF_MIN_SIM):
    """余弦 top-k 匹配；无达标项返回 []（调用方回退 DOMAIN_MAP）"""
    sims = []
    for hv, hyp in zip(hyp_vecs, hyps):
        if not hv or not intel_vec:
            continue
        a, b = (intel_vec, hv) if len(intel_vec) <= len(hv) else (hv, intel_vec)
        dot = sum(v * b.get(t, 0.0) for t, v in a.items())
        if dot >= min_sim:
            sims.append((dot, hyp))
    sims.sort(key=lambda x: -x[0])
    return [{"hyp_id": hyp["id"], "hyp_title": hyp.get("title", ""), "domains": [],
             "relevance_score": round(sim, 3), "method": "tfidf"}
            for sim, hyp in sims[:top_k]]


def _domain_hit_weight(text, keywords, kw_idf):
    """域内命中强度：按关键词 IDF 加权（2026-09-29 新增）。

    **问题**：旧版域匹配是"命中任一关键词即整域计入"，分数 = |交集|/|并集|。
    实测 `HM101_A_s2`（加拿大报复性关税涉及农产品）1197 条证据里 806 条
    relevance=1.0——因为"农产品"这个词把所有农产品期货日报、无关公告都拉进来。
    更泛的是 `AI`（命中 13.7% 语料）、`投资`（8.9%）、`美国`（8.0%）。

    **修法**：命中多个词比命中一个词强；命中专指词比命中泛词强。
    权重用 IDF：`w = log(N / (1 + df))`，df 是该词在全语料的文档频率。
    返回 [0,1] 归一化强度（域内最高权重词为 1.0）。
    """
    hits = [kw_idf.get(kw.lower(), 1.0) for kw in keywords if kw.lower() in text]
    if not hits:
        return 0.0, 0
    mx = max(kw_idf.values()) if kw_idf else 1.0
    # 多词命中累加（对数压缩，防"堆词"刷分），再按全局最高权重归一
    import math as _m
    strength = _m.log(1 + sum(hits)) / _m.log(1 + mx * 3)
    return min(strength, 1.0), len(hits)


def match_intel_to_hyp(intel, hyps, min_score=0.0, kw_idf=None):
    """Match intel to hypotheses using domain-level fuzzy matching
    min_score: 域重叠 Jaccard 下限（0=旧行为；收紧准入时传 DOMAIN_MIN_SCORE）
    kw_idf: 关键词 IDF 权重表（None 时退化为旧行为，保持兼容）"""
    intel_text = (intel.get("cn_title", "") + " " + intel.get("cn_summary", "") + " " + intel.get("title", "")).lower()
    _idf = kw_idf or {}

    # Find which domains the intel belongs to（带命中强度）
    intel_domains = {}
    for domain, keywords in DOMAIN_MAP.items():
        w, n = _domain_hit_weight(intel_text, keywords, _idf)
        if n:
            intel_domains[domain] = w

    if not intel_domains:
        return []

    matches = []
    for hyp in hyps:
        hyp_text = (hyp.get("title", "") + " " + (hyp.get("rationale") or "")).lower()

        # Find which domains the hypothesis belongs to
        hyp_domains = {}
        for domain, keywords in DOMAIN_MAP.items():
            w, n = _domain_hit_weight(hyp_text, keywords, _idf)
            if n:
                hyp_domains[domain] = w

        # Score: 交集域的平均强度 × 覆盖率（2026-09-29 由纯 Jaccard 改为强度加权）
        overlap = set(intel_domains) & set(hyp_domains)
        if overlap:
            strength = sum(min(intel_domains[d], hyp_domains[d]) for d in overlap) / len(overlap)
            coverage = len(overlap) / max(len(set(intel_domains) | set(hyp_domains)), 1)
            score = strength * (0.5 + 0.5 * coverage)
            if score >= min_score:
                matches.append({
                    "hyp_id": hyp["id"],
                    "hyp_title": hyp["title"],
                    "domains": list(overlap),
                    "relevance_score": round(score, 3)
                })
    
    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    return matches[:3]

def _ach_eligible(match_info):
    """单条匹配是否值得进 ACH 诊断队列（TF-IDF 命中一律是；DOMAIN 兜底按分数线）"""
    if match_info.get("method") == "tfidf":
        return True
    try:
        return float(match_info.get("relevance_score") or 0) >= ACH_ELIGIBLE_MIN
    except (TypeError, ValueError):
        return False


def update_hyp_evidence(hyp, intel, match_info):
    """Update hypothesis evidence_log"""
    if "evidence_log" not in hyp:
        hyp["evidence_log"] = []

    today = datetime.now().strftime("%Y-%m-%d")
    intel_id = intel.get("id", "")
    story_id = intel.get("story_id")  # P0-3 事件聚类：同事件多家报道只记一条证据

    # Check if already logged
    for entry in hyp["evidence_log"]:
        if intel_id in entry.get("intel_ids", []):
            return 0
        if story_id and story_id in (entry.get("story_ids") or []):
            return 0  # 同一事件已记过证据，防 10 家媒体灌 10 条重复证据稀释 ACH 矩阵

    entry = {
        "date": today,
        "intel_ids": [intel_id],
        "story_ids": [story_id] if story_id else [],
        "summary": (intel.get("cn_title", "") or intel.get("title", ""))[:100],
        # 2026-09-28 加 body：此前只存标题[:100]，JEV 判定时看不到正文，
        # 只能靠字面词做判断——实测「A股军工板块拉升」（gate=0.13, conf=0.93）
        # 被判成"台海冲突升级"的支持证据，因为它字面含"军工"。
        # 存 content_preview 让判定层能看到上下文（行情播报/他国事件等）。
        "body": (intel.get("cn_summary") or intel.get("content_preview")
                 or intel.get("content") or "")[:400],
        "domains": match_info.get("domains", []),
        "relevance": match_info.get("relevance_score", 0),
        # ACH 诊断准入标记：TF-IDF 命中一律 eligible；DOMAIN 兜底按分数线
        # （存量条目无此字段，ach_matrix 按其自己的存量规则判定）
        "ach_eligible": _ach_eligible(match_info),
        "source": intel.get("source_name", ""),
        "impact": intel.get("impact", "")[:200]
    }
    # 上卷来源标记（2026-09-29）：该证据是通过子节点匹配镜像到本节点的。
    # 保留来源信息供下游区分「直接匹配」与「子节点上卷」。
    if match_info.get("from_child"):
        entry["from_child"] = match_info["from_child"]
    hyp["evidence_log"].append(entry)

    # 2026-09-28 移除"涨跌词计数 ±0.01"置信度调整。
    # 原实现按情报正文里的涨跌词（增长/上升/下跌/暴跌…）增减 confidence，
    # 对全部 75 个节点生效——包括从未进过 ACH 矩阵的 mega 探针。
    # 实测后果：`三战在5年内爆发` 先验 0.05 被推到 0.94，而贡献它的证据是
    # 「大熊猫抵达美国」「国债收益率走高」这类与议题无关的条目——涨跌词
    # 衡量的是行情语气，不是"这条情报是否支持该假设"，两者无因果。
    # 置信度现只由两个正经来源写：ACH 贝叶斯后验（ach_matrix.bayesian_update）
    # 与周循环 AI 裁判（hypothesis_engine.verify_hypothesis）。此处只挂证据。
    return 1

def main():
    with open(HYP_FILE, "r", encoding="utf-8") as f:
        hyps = json.load(f)

    # 取最近 3 天的正式情报文件。
    #
    # **必须按日期排序且排除 raw/final（2026-09-30 修）**：原实现是
    # `sorted(glob("intel_*.jsonl"), reverse=True)[:3]`，字符串排序会把
    # `intel_final_20260829.jsonl`（`f` > `2`）排在 `intel_2026*` 之前，
    # 于是那个一个月前的 5 条残留文件占掉 3 个名额之一 —— 实测生产只读到
    # 2 天数据（9-29 的 2796 条被挤掉）。`tools/fetch_now.py` 的注释写明
    # `intel_raw_*`/`intel_final_*` 不参与下游，这里此前没落实。
    intel_files = [f for f in sorted(INTEL_DIR.glob("intel_2*.jsonl"), reverse=True)
                   if "raw" not in f.name and "final" not in f.name][:3]
    all_intel = []
    for f in intel_files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        all_intel.append(json.loads(line))
                    except:
                        pass
    print(f"[LINK] 读取 {len(intel_files)} 个情报文件: "
          + ", ".join(f.name for f in intel_files))

    # P0-3 事件聚类 + P1-1 TF-IDF 向量：共用 cluster_stories 分词器（失败不阻塞，退化原行为）
    cs = _load_cluster_module()
    if cs:
        try:
            print("story cluster:", cs.assign_story_ids(all_intel))
        except Exception as e:
            print(f"story cluster skipped: {e}")

    # P1-1: 情报+假设投同一 TF-IDF 空间（新假设零关键词成本接入证据链）
    tfidf_ready = False
    intel_vecs = hyp_vecs = None
    if cs:
        try:
            hyp_docs = []
            for h in hyps:
                # 指标名是假设文档里最具区分度的具体词（新闻标题常直接含它们），必须进语料
                ind_names = " ".join(str(ind.get("name", "")) for ind in (h.get("indicators") or [])
                                     if isinstance(ind, dict))
                hyp_docs.append(cs._tokens(str(h.get("title", "")) + " " + str(h.get("core_claim") or "")
                                           + " " + str(h.get("rationale") or "")
                                           + " " + str(h.get("falsification_criteria") or "")[:200]
                                           + " " + ind_names))
            intel_docs = [cs._tokens(cs._item_text(it)) for it in all_intel]
            all_vecs = build_tfidf_vectors(hyp_docs + intel_docs)
            hyp_vecs = all_vecs[:len(hyps)]
            intel_vecs = all_vecs[len(hyps):]
            tfidf_ready = True
            print(f"[TFIDF] 向量就绪: {len(intel_docs)} 情报 × {len(hyps)} 假设")
        except Exception as e:
            print(f"[TFIDF] 向量构建失败, 全部走 DOMAIN_MAP 兜底: {e}")

    # 关键词 IDF 表（2026-09-29）：DOMAIN 兜底从"命中即满分"改为 IDF 加权。
    # 用全语料算文档频率——`AI`（命中 13.7%）`投资`（8.9%）`美国`（8.0%）
    # 这类泛词权重低，`台积电``霍尔木兹` 这类专指词权重高。
    #
    # ⚠️ 实测踩坑（2026-09-30）：公式 log(N/(1+df)) 在 df=0 时给出 log(N)≈9，
    # 但 df=0 意味着该词在本批语料里**从未出现**（如"台积电"当天无相关新闻）——
    # 给最高权重是错的，会让一个没出现的词主导匹配强度。
    # 修法：df=0 的词取 df=1 的值（视为极稀有但可命中），避免极端值。
    kw_idf = {}
    if cs:
        try:
            import math as _m
            n_docs = len(all_intel) or 1
            all_kws = {kw.lower() for kws in DOMAIN_MAP.values() for kw in kws}
            df = {kw: 0 for kw in all_kws}
            for it in all_intel:
                txt = cs._item_text(it).lower()
                for kw in all_kws:
                    if kw in txt:
                        df[kw] += 1
            kw_idf = {kw: _m.log(n_docs / (1.0 + max(c, 1))) for kw, c in df.items()}
            _zero = sum(1 for c in df.values() if c == 0)
            print(f"[DOMAIN] 关键词 IDF 表就绪: {len(kw_idf)} 词"
                  f"（泛词 AI={kw_idf.get('ai', 0):.2f} 投资={kw_idf.get('投资', 0):.2f}"
                  f" / 专指 芯片={kw_idf.get('芯片', 0):.2f}；df=0 的 {_zero} 词按 df=1 处理）")
        except Exception as e:
            print(f"[DOMAIN] IDF 表构建失败, 退化为旧行为: {e}")
            kw_idf = {}

    total_links = 0
    total_updates = 0
    tfidf_links = domain_links = 0
    link_report = []

    # Pass 1: 全量匹配 + 报告（不限量，报告反映完整匹配情况）
    candidates = []   # (hyp_id, intel, match)
    for idx, intel in enumerate(all_intel):
        matches = None
        if tfidf_ready:
            matches = match_intel_tfidf(intel_vecs[idx], hyp_vecs, hyps)
        if not matches:
            matches = match_intel_to_hyp(intel, hyps, min_score=DOMAIN_MIN_SCORE,
                                         kw_idf=kw_idf)
        if not matches:
            continue
        total_links += 1
        tfidf_links += sum(1 for m in matches if m.get("method") == "tfidf")
        domain_links += sum(1 for m in matches if m.get("method") != "tfidf")
        report_entry = {
            "intel_id": intel.get("id"),
            "intel_title": (intel.get("cn_title", "") or intel.get("title", ""))[:80],
            "matched_hyps": []
        }
        for match in matches[:2]:
            candidates.append((match["hyp_id"], intel, match))
            report_entry["matched_hyps"].append({
                "hyp_id": match["hyp_id"],
                "hyp_title": match["hyp_title"],
                "domains": match["domains"],
                "method": match.get("method", "domain"),
                "relevance_score": match.get("relevance_score", 0)
            })
        link_report.append(report_entry)

    # Pass 2: 每假设每日 cap 择优记录（2026-09-12 证据准入收紧）。
    # update_hyp_evidence 内部按 intel_id/story_id 幂等去重，重复条目不计入 cap。
    #
    # **cap 必须按"已存在的今日条目"计数，不能只数本轮新增（2026-09-30 修）**：
    # 原实现 `hyp_recorded` 是**本轮局部**计数器，每小时 refresh 各跑一轮 →
    # 每轮都能各加 6 条 → 实测 HM100 单日直接证据 9 条、HM101 10 条，全超 cap。
    # 这与 Pass 3 上卷失控是同一根因的两面：**"每日上限"必须跨轮次累计**。
    #
    # **排序键必须用实测有区分力的信号（2026-09-30 修）**：
    # 原键 = (tfidf 优先, -relevance, -base_score)。但实测（tools/find_ranking_signal.py，
    # 5175 条已诊断证据 join 回原始情报，用 JEV gate 作真值）：
    #     relevance   AUC=0.4762  ← **低于随机**，且 51.4% 并列在 1.0（分数饱和）
    #     kw_hits*10 + body_len/100  AUC=0.6458  并列仅 4.2%
    # `tools/test_sort_value.py` 更直接：现状 top-6 只抓到理想值的 **8%**，
    # 比随机选（12%）还差——因为 86% 的证据并列满分，排序退化成随机抽签。
    # 新键用 `keywords_hit`（情报命中的关键词数，直接反映该条与本项目议题的
    # 相关强度）+ `body_len`（正文长度，破并列且与信息量正相关）。
    hyp_by_id = {h["id"]: h for h in hyps}
    _today = datetime.now().strftime("%Y-%m-%d")
    hyp_recorded = {
        h["id"]: sum(1 for e in (h.get("evidence_log") or [])
                     if str(e.get("date")) == _today and not e.get("from_child"))
        for h in hyps
    }

    def _sel_key(c):
        hyp_id, intel, match = c
        # 主键：情报关键词命中数（实测 AUC 0.62，唯一的强信号）
        try:
            kw = len(intel.get("keywords_hit") or [])
        except (TypeError, ValueError):
            kw = 0
        # 次键：正文长度（破 kw 并列；实测 AUC 0.59）
        try:
            blen = len(str(intel.get("cn_summary") or intel.get("content_preview")
                            or intel.get("content") or ""))
        except (TypeError, ValueError):
            blen = 0
        return (0 if match.get("method") == "tfidf" else 1,
                -(kw * 10 + blen / 100.0))

    for hyp_id, intel, match in sorted(candidates, key=_sel_key):
        if hyp_recorded.get(hyp_id, 0) >= EVIDENCE_DAILY_CAP:
            continue
        hyp = hyp_by_id.get(hyp_id)
        if not hyp:
            continue
        updated = update_hyp_evidence(hyp, intel, match)
        total_updates += updated
        if updated:
            hyp_recorded[hyp_id] = hyp_recorded.get(hyp_id, 0) + 1

    # Pass 3: 证据上卷（2026-09-29）——子节点的证据同时挂到可诊断祖先。
    #
    # **动因**：全树 18,178 条证据里 71%（13,004 条）挂在 major 之外的节点上，
    # 而 ACH 只诊断 major（`ach_daily_batch` 按 level=='major' 筛选）——
    # 这些挂载消耗了 TF-IDF 算力，却没有任何下游消费。
    # 典型：`HM102_A`（AI芯片供给瓶颈）挂 1514 条，其父 `HM102` 只诊断自己的 1526 条。
    #
    # **为什么上卷而非把子节点升为 major**：子命题是父节点的**验证分解**
    # （indicators 是可查证的量化检查点），不是独立竞争假设。父子同时在 ACH 里
    # 会"自己和自己竞争"，违背 ACH 的互斥要求（Heuer）。
    #
    # **为什么不在 Pass 1 直接双挂**：cap 是按假设计的，Pass 1 双挂会挤占
    # 父节点自己的名额。这里在 cap 之后做**镜像**，且带 `from_child` 标记——
    # 父节点择优时优先自己的直接证据。
    #
    # **上卷必须有每日上限（2026-09-30 补）**：上卷本身有效（实测上卷证据的
    # 信号率 11.8%，与直接证据 13.1% 几乎持平），但不限量时量会失控——Pass 3
    # 遍历的是**全量 candidates**，每个子节点的每条匹配都镜像到祖先。实测
    # 9-29 单日给 HM101 灌 481 条（350 条来自 HM101_A_s2 一个子节点），
    # JEV 日消耗从 5 万 token 涨到 798 万（100 倍）——按 $0.042/Mtok 算，
    # 用户的 $5 额度只够 13 天。**"能挂上"不等于"该挂上"**。
    #
    # cap 取 4 的依据：6 major ×（直接 6 + 上卷 4）= 60/天 = `ach_daily_batch`
    # 的 BATCH 容量（证据量必须与诊断吞吐匹配，多出的只会积压成矩阵膨胀）。
    # 上卷低于直接是刻意的——直接证据匹配的是 major 自己，上卷隔了一层子节点，
    # 优先级应当更低（与 Pass 2 排序"直接优先"的取向一致）。
    ROLLUP_MAX_LEVEL = {"major"}   # 上卷终点：可诊断层级
    ROLLUP_DAILY_CAP = 4           # 每祖先每日上卷上限（跨多次运行幂等：按已存在的今日上卷条目计数）
    today_str = datetime.now().strftime("%Y-%m-%d")
    rolled_today = {}
    for _h in hyps:
        if _h.get("level") in ROLLUP_MAX_LEVEL:
            rolled_today[_h["id"]] = sum(
                1 for e in (_h.get("evidence_log") or [])
                if e.get("from_child") and str(e.get("date")) == today_str)
    rolled = 0
    for hyp_id, intel, match in sorted(candidates, key=_sel_key):
        child = hyp_by_id.get(hyp_id)
        if not child:
            continue
        # 沿 parent 链上溯，找到第一个可诊断祖先
        seen_anc = set()
        anc_id = child.get("parent")
        while anc_id and anc_id not in seen_anc:
            seen_anc.add(anc_id)
            anc = hyp_by_id.get(anc_id)
            if not anc:
                break
            if anc.get("level") in ROLLUP_MAX_LEVEL:
                if rolled_today.get(anc["id"], 0) >= ROLLUP_DAILY_CAP:
                    break
                # 镜像一条证据到祖先（标记来源，供下游区分）
                mirror = dict(match)
                mirror["from_child"] = hyp_id
                mirror["relevance_score"] = min(
                    float(match.get("relevance_score") or 0) * 0.9, 1.0)
                _n = update_hyp_evidence(anc, intel, mirror)
                rolled += _n
                if _n:
                    rolled_today[anc["id"]] = rolled_today.get(anc["id"], 0) + 1
                break
            anc_id = anc.get("parent")

    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    OUTPUT_FILE.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "total_intel": len(all_intel),
        "linked_intel": total_links,
        "evidence_updates": total_updates,
        "rolled_up": rolled,
        "links": link_report[:50]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"Linked: {total_links}/{len(all_intel)} intel items")
    print(f"Evidence updates: {total_updates}")
    print(f"Match method: tfidf {tfidf_links} / domain 兜底 {domain_links}")
    print(f"[LINK] 证据准入: 候选 {len(candidates)} → 记录 {total_updates} (cap={EVIDENCE_DAILY_CAP}/假设/日)")
    print(f"[LINK] 证据上卷: {rolled} 条子节点证据镜像到可诊断祖先 (cap={ROLLUP_DAILY_CAP}/假设/日)")

if __name__ == "__main__":
    main()
