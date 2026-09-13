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
EVIDENCE_DAILY_CAP = 6       # 每假设每日新增证据上限：8 major × 6 = ≤48/天，匹配 ACH 诊断速度(~40/天)
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


def match_intel_to_hyp(intel, hyps, min_score=0.0):
    """Match intel to hypotheses using domain-level fuzzy matching
    min_score: 域重叠 Jaccard 下限（0=旧行为；收紧准入时传 DOMAIN_MIN_SCORE）"""
    intel_text = (intel.get("cn_title", "") + " " + intel.get("cn_summary", "") + " " + intel.get("title", "")).lower()
    
    # Find which domains the intel belongs to
    intel_domains = set()
    for domain, keywords in DOMAIN_MAP.items():
        for kw in keywords:
            if kw.lower() in intel_text:
                intel_domains.add(domain)
                break
    
    if not intel_domains:
        return []
    
    matches = []
    for hyp in hyps:
        hyp_text = (hyp.get("title", "") + " " + hyp.get("rationale", "")).lower()
        
        # Find which domains the hypothesis belongs to
        hyp_domains = set()
        for domain, keywords in DOMAIN_MAP.items():
            for kw in keywords:
                if kw.lower() in hyp_text:
                    hyp_domains.add(domain)
                    break
        
        # Score based on domain overlap
        domain_overlap = intel_domains & hyp_domains
        if domain_overlap:
            score = len(domain_overlap) / max(len(intel_domains | hyp_domains), 1)
            if score >= min_score:
                matches.append({
                    "hyp_id": hyp["id"],
                    "hyp_title": hyp["title"],
                    "domains": list(domain_overlap),
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
        "domains": match_info.get("domains", []),
        "relevance": match_info.get("relevance_score", 0),
        # ACH 诊断准入标记：TF-IDF 命中一律 eligible；DOMAIN 兜底按分数线
        # （存量条目无此字段，ach_matrix 按其自己的存量规则判定）
        "ach_eligible": _ach_eligible(match_info),
        "source": intel.get("source_name", ""),
        "impact": intel.get("impact", "")[:200]
    }
    hyp["evidence_log"].append(entry)
    
    # Adjust confidence
    impact_text = (intel.get("impact", "") + " " + intel.get("cn_summary", "")).lower()
    direction = hyp.get("direction", "toward")
    
    support_w = ["增长", "上升", "加速", "扩大", "增加", "提升", "加强", "突破", "创新高"]
    contradict_w = ["下降", "减少", "放缓", "收缩", "降低", "减弱", "恶化", "下跌", "暴跌"]
    
    supports = sum(1 for w in support_w if w in impact_text)
    contradicts = sum(1 for w in contradict_w if w in impact_text)
    
    old_conf = hyp.get("confidence", 0.5)
    if supports > contradicts:
        new_conf = min(0.95, old_conf + 0.01)
    elif contradicts > supports:
        new_conf = max(0.05, old_conf - 0.01)
    else:
        new_conf = old_conf
    
    hyp["confidence"] = round(new_conf, 3)
    return 1

def main():
    with open(HYP_FILE, "r", encoding="utf-8") as f:
        hyps = json.load(f)
    
    intel_files = sorted(INTEL_DIR.glob("intel_*.jsonl"), reverse=True)[:3]
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
            matches = match_intel_to_hyp(intel, hyps, min_score=DOMAIN_MIN_SCORE)
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
    # 排序：TF-IDF 命中优先（语义匹配）→ DOMAIN 分数降序 → 情报关键词密度(base_score)降序。
    # update_hyp_evidence 内部按 intel_id/story_id 幂等去重，重复条目不计入 cap。
    hyp_by_id = {h["id"]: h for h in hyps}
    hyp_recorded = {}

    def _sel_key(c):
        hyp_id, intel, match = c
        try:
            base = float(intel.get("base_score") or 0)
        except (TypeError, ValueError):
            base = 0.0
        return (0 if match.get("method") == "tfidf" else 1,
                -float(match.get("relevance_score") or 0), -base)

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
    
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    OUTPUT_FILE.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "total_intel": len(all_intel),
        "linked_intel": total_links,
        "evidence_updates": total_updates,
        "links": link_report[:50]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"Linked: {total_links}/{len(all_intel)} intel items")
    print(f"Evidence updates: {total_updates}")
    print(f"Match method: tfidf {tfidf_links} / domain 兜底 {domain_links}")
    print(f"[LINK] 证据准入: 候选 {len(candidates)} → 记录 {total_updates} (cap={EVIDENCE_DAILY_CAP}/假设/日)")

if __name__ == "__main__":
    main()
