"""宏观五维分析框架 (read-macro 下沉)
- MACRO_FIVE_DIM: 五维定义常量, 注入 analyze.py 的 system prompt
- build_macro_state_prompt(): 把当前指标快照转成"宏观状态一句话"

五维来源: read-macro 插件 skills/macro-dashboard (增长/通胀/货币与流动性/信用/外部)
采用"宏量合并"策略: 只注入框架名 + 当前指标快照, 让 AI 自行展开研判,
避免 800 字硬编码与 read-macro SKILL.md 重复维护。
"""
from pathlib import Path
import json

MACRO_FIVE_DIM = (
    "五维宏观框架（每条情报研判时先过一遍宏观面过滤）：\n"
    "1. 增长 — GDP/PMI/工业增加值：当前增长动能在哪个象限\n"
    "2. 通胀 — CPI/PPI：通胀是需求问题还是供给问题\n"
    "3. 货币与流动性 — 7天逆回购利率(政策锚)/Shibor(银行间)/M1/M2：央行松紧取向\n"
    "4. 信用 — 社融总量/M1-M2剪刀差：实体经济拿到钱没有\n"
    "5. 外部 — 美元/美债/外部冲击(关税/地缘)：外溢传导路径\n"
    "研判时标注 [披露](官方数据)/[测算](由数据推算)/[推断](逻辑外推) 三层置信标签。"
)

# 五维对应的 macro_indicators.json 指标 id 映射
DIM_TO_INDICATORS = {
    "增长": ["cn_gdp_growth", "kr_gdp_growth"],
    "通胀": ["cn_cpi", "us_cpi"],
    "货币与流动性": ["cn_dr007", "cn_shibor_3m", "cn_m1_yoy", "cn_m2_yoy", "us_fed_funds"],
    "信用": ["cn_shrong_yoy"],
    "外部": ["us_10y", "dxy_proxy", "fx_usd_cny"],
}


def build_macro_state_prompt(macro_file=None):
    """读 macro_indicators.json, 生成'宏观状态一句话'注入 prompt。
    数据缺失时返回空串, 不阻塞分析。
    """
    if macro_file is None:
        macro_file = Path(r"D:\osint\data\macro_indicators.json")
    try:
        data = json.loads(Path(macro_file).read_text(encoding="utf-8"))
    except Exception:
        return ""

    inds = data.get("indicators", {})
    if not inds:
        return ""

    lines = []
    for dim, ids in DIM_TO_INDICATORS.items():
        parts = []
        for iid in ids:
            rec = inds.get(iid)
            if not rec or rec.get("value") is None:
                continue
            stale_mark = "[stale]" if rec.get("stale") else ""
            # 派生变化 (环比/同比), fetch_macro_indicators.py 写盘时存了 change_bp/change_pct
            change = ""
            if "change_bp" in rec:
                bp = rec["change_bp"]
                sign = "+" if bp > 0 else ""
                change = f"({sign}{bp}bp)"
            elif "change_pct" in rec:
                pct = rec["change_pct"]
                sign = "+" if pct > 0 else ""
                change = f"({sign}{pct})"
            parts.append(f"{rec.get('label','')}={rec.get('value')}{change}{stale_mark}")
        if parts:
            lines.append(f"{dim}: " + "；".join(parts))
    if not lines:
        return ""
    return "当前宏观快照（" + data.get("updated_at", "")[:10] + "）：" + " | ".join(lines)
