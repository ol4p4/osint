import json, re, sys
from pathlib import Path
from datetime import datetime

HYP_FILE = Path(r"D:\Codex输出\osint_卫星图\hypotheses\active_hypotheses.json")
INTEL_DIR = Path(r"D:\Codex输出\osint_卫星图")
OUTPUT_FILE = Path(r"D:\Codex输出\osint_卫星图\link_report.json")

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

def match_intel_to_hyp(intel, hyps):
    """Match intel to hypotheses using domain-level fuzzy matching"""
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
            matches.append({
                "hyp_id": hyp["id"],
                "hyp_title": hyp["title"],
                "domains": list(domain_overlap),
                "relevance_score": round(score, 3)
            })
    
    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    return matches[:3]

def update_hyp_evidence(hyp, intel, match_info):
    """Update hypothesis evidence_log"""
    if "evidence_log" not in hyp:
        hyp["evidence_log"] = []
    
    today = datetime.now().strftime("%Y-%m-%d")
    intel_id = intel.get("id", "")
    
    # Check if already logged
    for entry in hyp["evidence_log"]:
        if intel_id in entry.get("intel_ids", []):
            return 0
    
    entry = {
        "date": today,
        "intel_ids": [intel_id],
        "summary": (intel.get("cn_title", "") or intel.get("title", ""))[:100],
        "domains": match_info.get("domains", []),
        "relevance": match_info.get("relevance_score", 0),
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
    
    total_links = 0
    total_updates = 0
    link_report = []
    
    for intel in all_intel:
        matches = match_intel_to_hyp(intel, hyps)
        if matches:
            total_links += 1
            report_entry = {
                "intel_id": intel.get("id"),
                "intel_title": (intel.get("cn_title", "") or intel.get("title", ""))[:80],
                "matched_hyps": []
            }
            
            for match in matches[:2]:
                hyp = next((h for h in hyps if h["id"] == match["hyp_id"]), None)
                if hyp:
                    updated = update_hyp_evidence(hyp, intel, match)
                    total_updates += updated
                    report_entry["matched_hyps"].append({
                        "hyp_id": match["hyp_id"],
                        "hyp_title": match["hyp_title"],
                        "domains": match["domains"],
                        "new_confidence": hyp.get("confidence")
                    })
            
            link_report.append(report_entry)
    
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

if __name__ == "__main__":
    main()
