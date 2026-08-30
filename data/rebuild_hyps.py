import json, uuid
from pathlib import Path

HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")

with open(HYP_FILE, "r", encoding="utf-8") as f:
    existing = json.load(f)

# Index existing by ID
by_id = {h["id"]: h for h in existing}

# Define the correct tree structure
# True majors -> medium -> small
TREE = {
    "hyp_e2b46e32": {
        "level": "major",
        "title": "东亚三国现代化进程趋同",
        "children": {
            "hyp_e2b46e32_sub1": {"title": "中日韩面临相似老龄化趋势，实施可比政策应对", "direction": "toward", "confidence": 0.78,
                "children": [
                    {"title": "老年抚养比到2030年三国均超20%", "direction": "toward", "confidence": 0.75, "indicator": "老年抚养比(65+/15-64)", "source": "世界银行"},
                    {"title": "延迟退休/养老金改革政策趋同", "direction": "toward", "confidence": 0.7, "indicator": "法定退休年龄变化", "source": "各国人社部"},
                ]},
            "hyp_e2b46e32_sub2": {"title": "青年失业率结构性问题趋同", "direction": "toward", "confidence": 0.72,
                "children": [
                    {"title": "青年NEET率东亚三国同步上升", "direction": "toward", "confidence": 0.68, "indicator": "青年NEET率", "source": "ILO/OECD"},
                    {"title": "学历通胀导致就业门槛同步提高", "direction": "toward", "confidence": 0.65, "indicator": "本科以上就业占比", "source": "各国统计局"},
                ]},
            "hyp_e2b46e32_sub3": {"title": "房地产泡沫与消费萎缩路径相似", "direction": "toward", "confidence": 0.68,
                "children": [
                    {"title": "房价收入比持续高位", "direction": "toward", "confidence": 0.72, "indicator": "房价收入比", "source": "Demographia"},
                    {"title": "居民消费率持续走低", "direction": "toward", "confidence": 0.66, "indicator": "最终消费占GDP比重", "source": "世界银行"},
                ]},
            "hyp_e2b46e32_sub4": {"title": "产业政策主导的高科技追赶策略趋同", "direction": "toward", "confidence": 0.7,
                "children": [
                    {"title": "半导体/新能源/AI成为国家战略重点", "direction": "toward", "confidence": 0.8, "indicator": "研发支出占GDP比", "source": "OECD"},
                    {"title": "政府补贴占企业营收比上升", "direction": "toward", "confidence": 0.65, "indicator": "产业补贴金额", "source": "各国财政部"},
                ]},
        }
    },
    "hyp_d26158d3": {
        "level": "major",
        "title": "中国社保走韩国老路",
        "children": {
            "hyp_d26158d3_sub1": {"title": "社保费率先降后升的周期", "direction": "toward", "confidence": 0.7,
                "children": [
                    {"title": "企业社保缴费率在2025-2027年触底", "direction": "toward", "confidence": 0.72, "indicator": "企业养老保险缴费率", "source": "人社部"},
                    {"title": "2028年后费率回升压力增大", "direction": "toward", "confidence": 0.65, "indicator": "社保基金收支比", "source": "财政部"},
                ]},
            "hyp_d26158d3_sub2": {"title": "养老金替代率持续下降", "direction": "toward", "confidence": 0.75,
                "children": [
                    {"title": "城镇职工养老金替代率跌破40%", "direction": "toward", "confidence": 0.7, "indicator": "养老金替代率", "source": "人社部"},
                    {"title": "个人养老账户收益率跑输通胀", "direction": "toward", "confidence": 0.68, "indicator": "个人养老金收益率", "source": "银保监会"},
                ]},
            "hyp_d26158d3_sub3": {"title": "延迟退休推进节奏与韩国1997-2010类似", "direction": "toward", "confidence": 0.65,
                "children": [
                    {"title": "渐进式延迟退休方案2025年落地", "direction": "toward", "confidence": 0.8, "indicator": "法定退休年龄", "source": "全国人大"},
                    {"title": "实际退休年龄与法定退休年龄差距扩大", "direction": "toward", "confidence": 0.6, "indicator": "实际退休年龄", "source": "统计局"},
                ]},
            "hyp_d26158d3_sub4": {"title": "社保基金缺口扩大引发财政压力", "direction": "toward", "confidence": 0.72,
                "children": [
                    {"title": "养老金累计结余增速放缓", "direction": "toward", "confidence": 0.75, "indicator": "养老金累计结余", "source": "财政部"},
                    {"title": "中央转移支付占社保收入比上升", "direction": "toward", "confidence": 0.7, "indicator": "财政补贴社保金额", "source": "财政部"},
                ]},
        }
    },
    "HM100": {
        "level": "major",
        "title": "全球能源转型加速导致传统能源需求骤降",
        "children": {
            "HM100_A": {"title": "新能源装机量超预期增长", "direction": "toward", "confidence": 0.78,
                "children": [
                    {"title": "太阳能+风电年装机超500GW", "direction": "toward", "confidence": 0.8, "indicator": "全球新增可再生能源装机", "source": "IEA"},
                    {"title": "储能装机量年增60%以上", "direction": "toward", "confidence": 0.72, "indicator": "全球储能装机容量", "source": "BloombergNEF"},
                ]},
            "HM100_B": {"title": "传统能源企业估值重估", "direction": "toward", "confidence": 0.7,
                "children": [
                    {"title": "石油公司EV/EBITDA低于历史均值", "direction": "toward", "confidence": 0.68, "indicator": "石油行业估值倍数", "source": "Bloomberg"},
                    {"title": "煤炭企业融资成本上升", "direction": "toward", "confidence": 0.72, "indicator": "煤炭企业债券利差", "source": "Wind"},
                ]},
        }
    },
    "HM101": {
        "level": "major",
        "title": "美加贸易摩擦升级引发全球供应链重构",
        "children": {
            "HM101_A": {"title": "北美自贸区实际瓦解", "direction": "toward", "confidence": 0.65,
                "children": [
                    {"title": "美国对加拿大汽车关税超25%", "direction": "toward", "confidence": 0.6, "indicator": "美加汽车关税税率", "source": "美国贸易代表"},
                    {"title": "加拿大对美报复性关税涉及农产品", "direction": "toward", "confidence": 0.55, "indicator": "加拿大报复关税清单", "source": "加拿大财政部"},
                ]},
            "HM101_B": {"title": "供应链向东南亚/印度转移加速", "direction": "toward", "confidence": 0.72,
                "children": [
                    {"title": "越南/印度制造业FDI年增30%以上", "direction": "toward", "confidence": 0.7, "indicator": "东南亚制造业FDI", "source": "UNCTAD"},
                    {"title": "中国对美出口占比降至15%以下", "direction": "toward", "confidence": 0.65, "indicator": "中国对美出口占比", "source": "海关总署"},
                ]},
        }
    },
    "HM102": {
        "level": "major",
        "title": "AI算力与关键材料短缺限制技术商业化速度",
        "children": {
            "HM102_A": {"title": "AI芯片供给瓶颈持续", "direction": "toward", "confidence": 0.72,
                "children": [
                    {"title": "台积电先进制程产能利用率超95%", "direction": "toward", "confidence": 0.75, "indicator": "台积电产能利用率", "source": "台积电财报"},
                    {"title": "HBM内存价格年涨50%以上", "direction": "toward", "confidence": 0.7, "indicator": "HBM内存合约价", "source": "TrendForce"},
                ]},
            "HM102_B": {"title": "关键矿物（稀土/锂/钴）供应受限", "direction": "toward", "confidence": 0.68,
                "children": [
                    {"title": "中国稀土出口管制收紧", "direction": "toward", "confidence": 0.72, "indicator": "稀土出口配额", "source": "商务部"},
                    {"title": "锂矿价格波动加剧", "direction": "toward", "confidence": 0.65, "indicator": "碳酸锂现货价", "source": "SMM"},
                ]},
        }
    },
    "HM001": {
        "level": "major",
        "title": "台海冲突升级风险",
        "children": {
            "HM001_A": {"title": "军事对峙常态化", "direction": "toward", "confidence": 0.55,
                "children": [
                    {"title": "台海周边军事演习频次翻倍", "direction": "toward", "confidence": 0.6, "indicator": "台海军演次数", "source": "国防部"},
                    {"title": "美国对台军售金额创新高", "direction": "toward", "confidence": 0.55, "indicator": "美国对台军售额", "source": "美国国防部"},
                ]},
            "HM001_B": {"title": "经济制裁风险上升", "direction": "toward", "confidence": 0.5,
                "children": [
                    {"title": "西方对中国金融制裁方案储备", "direction": "toward", "confidence": 0.45, "indicator": "制裁相关立法进展", "source": "美国国会"},
                    {"title": "台海冲突导致全球芯片供应链中断", "direction": "toward", "confidence": 0.4, "indicator": "台湾半导体全球份额", "source": "TrendForce"},
                ]},
        }
    },
    "HM002": {
        "level": "major",
        "title": "全球能源供应格局重塑导致大国竞争",
        "children": {
            "HM002_A": {"title": "霍尔木兹海峡通航受阻常态化", "direction": "toward", "confidence": 0.6,
                "children": [
                    {"title": "伊朗封锁威胁频率增加", "direction": "toward", "confidence": 0.55, "indicator": "霍尔木兹海峡事件次数", "source": "CENTCOM"},
                    {"title": "中国原油进口绕道成本上升15%", "direction": "toward", "confidence": 0.58, "indicator": "中国原油进口运费", "source": "海关总署"},
                ]},
            "HM002_B": {"title": "能源独立成为国家战略优先", "direction": "toward", "confidence": 0.7,
                "children": [
                    {"title": "中国核电审批加速（年批6台以上）", "direction": "toward", "confidence": 0.72, "indicator": "核电审批数量", "source": "能源局"},
                    {"title": "国内油气产量目标上调", "direction": "toward", "confidence": 0.68, "indicator": "国内原油产量", "source": "能源局"},
                ]},
        }
    },
    "HM003": {
        "level": "major",
        "title": "AI技术成本上升触发宏观经济波动与金融风险",
        "children": {
            "HM003_A": {"title": "AI资本开支泡沫化", "direction": "toward", "confidence": 0.55,
                "children": [
                    {"title": "科技巨头AI资本开支占营收比超30%", "direction": "toward", "confidence": 0.5, "indicator": "科技巨头AI CapEx占比", "source": "财报"},
                    {"title": "AI初创企业估值回调50%以上", "direction": "toward", "confidence": 0.45, "indicator": "AI初创估值中位数", "source": "Crunchbase"},
                ]},
            "HM003_B": {"title": "AI替代就业引发社会成本", "direction": "toward", "confidence": 0.6,
                "children": [
                    {"title": "白领岗位AI替代率年增10%", "direction": "toward", "confidence": 0.55, "indicator": "AI替代岗位数量", "source": "麦肯锡"},
                    {"title": "再培训成本由企业/政府分担比例上升", "direction": "toward", "confidence": 0.5, "indicator": "再培训投入金额", "source": "人社部"},
                ]},
        }
    },
}

# Build the new tree
new_hyps = []

for major_id, major_data in TREE.items():
    major_entry = {
        "id": major_id,
        "level": "major",
        "title": major_data["title"],
        "direction": "toward",
        "confidence": by_id.get(major_id, {}).get("confidence", 0.6),
        "base_confidence": by_id.get(major_id, {}).get("confidence", 0.6),
        "deadline": by_id.get(major_id, {}).get("deadline", ""),
        "status": "active",
        "rationale": by_id.get(major_id, {}).get("rationale", ""),
        "children": list(major_data["children"].keys()),
        "indicators": [],
        "falsification_criteria": "",
        "created": "2026-08-20",
        "evidence_log": [],
        "version": 3
    }
    new_hyps.append(major_entry)

    for med_id, med_data in major_data["children"].items():
        med_entry = {
            "id": med_id,
            "level": "medium",
            "title": med_data["title"],
            "direction": med_data.get("direction", "toward"),
            "confidence": med_data.get("confidence", 0.6),
            "base_confidence": med_data.get("confidence", 0.6),
            "deadline": "",
            "status": "active",
            "rationale": "",
            "parent": major_id,
            "children": [f"{med_id}_s{i+1}" for i in range(len(med_data.get("children", [])))],
            "weight": 0.5,
            "indicators": [],
            "falsification_criteria": "",
            "created": "2026-08-20",
            "evidence_log": [],
            "version": 1
        }
        new_hyps.append(med_entry)

        for i, small_data in enumerate(med_data.get("children", [])):
            small_id = f"{med_id}_s{i+1}"
            small_entry = {
                "id": small_id,
                "level": "small",
                "title": small_data["title"],
                "direction": small_data.get("direction", "toward"),
                "confidence": small_data.get("confidence", 0.5),
                "base_confidence": small_data.get("confidence", 0.5),
                "deadline": "",
                "status": "active",
                "rationale": "",
                "parent": med_id,
                "children": [],
                "weight": 0.3,
                "indicators": [
                    {
                        "name": small_data.get("indicator", ""),
                        "source": small_data.get("source", ""),
                        "current_value": None,
                        "target_value": None,
                        "threshold_support": small_data["title"],
                        "threshold_refute": ""
                    }
                ],
                "falsification_criteria": "",
                "created": "2026-08-20",
                "evidence_log": [],
                "version": 1
            }
            new_hyps.append(small_entry)

with open(HYP_FILE, "w", encoding="utf-8") as f:
    json.dump(new_hyps, f, ensure_ascii=False, indent=2)

majors = sum(1 for h in new_hyps if h["level"] == "major")
mediums = sum(1 for h in new_hyps if h["level"] == "medium")
smalls = sum(1 for h in new_hyps if h["level"] == "small")
print(f"Rebuilt: {len(new_hyps)} total ({majors} major, {mediums} medium, {smalls} small)")
