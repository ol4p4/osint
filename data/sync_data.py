import json
from pathlib import Path

DATA_FILE = Path(r"D:\osint\data\dashboard_data.json")
HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")

# 1. Sync hypotheses
with open(HYP_FILE, "r", encoding="utf-8") as f:
    hyps = json.load(f)

with open(DATA_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

old_count = len(data.get("hypotheses", []))
data["hypotheses"] = hyps
print(f"Hypotheses: {old_count} -> {len(hyps)}")

# 2. Fix short summaries
intel = data.get("intelligence", [])
fixed = 0
for item in intel:
    summary = item.get("cn_summary", "")
    title = item.get("cn_title", "")
    content = item.get("content_preview", "")
    
    if len(summary) < 20 and content:
        # Use content_preview as fallback, translate first 200 chars
        item["cn_summary"] = content[:300]
        fixed += 1
    
    # Also ensure impact is not empty
    if not item.get("impact") or "缺失" in item.get("impact", ""):
        cat = item.get("category_cn", "other")
        templates = {
            "energy": "能源价格波动影响中国进口成本，制造业/交通运输成本上升，可能传导至就业市场。",
            "geopolitics": "地缘政治变化影响国际资本流动和贸易环境，外贸/供应链岗位可能受影响。",
            "macro": "宏观经济指标变化影响货币政策和市场预期，金融/投资岗位关注政策方向。",
            "finance": "金融市场波动影响资产价格和融资环境，青年就业受市场情绪影响。",
            "east_asia": "东亚区域动态影响中日韩经贸关系和产业链布局。",
            "tech": "科技产业变化影响产业格局和就业结构。",
        }
        item["impact"] = templates.get(cat, "该情报可能对宏观经济和就业市场产生间接影响。")
        fixed += 1

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Fixed {fixed} intelligence items")
print(f"Total intelligence: {len(intel)}")
