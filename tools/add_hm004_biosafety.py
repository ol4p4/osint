#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性脚本：新增 HM004「生物安全事件升级引发跨境防控收紧」major 假设节点（跑一次即弃）。

## 动机（2026-10-09）

俄罗斯伊尔库茨克防鼠疫研究所员工死亡事件（2026-10-02 首发）暴露了一个结构性缺口：
75 个假设节点里**零公共卫生节点**，导致 51 条相关情报在树上无处可挂，ACH 矩阵
7145 条证据里一条公卫证据都没有。ACH 只诊断 `level=='major'`（`ach_daily_batch.py:65`
与 `hypothesis_engine.py:410`），所以公卫领域要获得判定能力，必须建成 major。

## 为什么是「竞争面」而不是「事实描述」

AGENTS.md 明确：**major 层必须有竞争面**，ACH 方法论（Heuer）要求假设互斥、竞争
同一批证据；"事实描述"（如"会不会有疫情"）不合格。故本节点按三选一竞争假设设计：

  情景 A（外溢升级）—— 事件频次上升 + 跨境防控实质收紧
  情景 B（可控收敛）—— 个案局限 + 防控按既有机制运行，无跨境升级
  情景 C（信息不透明）—— 官方口径反复/信息封锁，风险无法评估

三个子节点即三条竞争路径，证据挂到 HM004 后由 JEV 按 gate 判定哪条被推动。

## 幂等与安全

- HM004 已存在 → 直接跳过，不重复写入
- 写前自动备份 `active_hypotheses.json.bak_pre_hm004_<时间戳>`
- 结构自校验（level/indicators 的 threshold_refute/证伪判据含年份或数值）不过则不动原文件
- 写入用临时文件 + 原子替换，避免半写坏文件

用法：
    python tools/add_hm004_biosafety.py --dry     # 只看将写入的节点
    python tools/add_hm004_biosafety.py           # 实际写入
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HYP_FILE = ROOT / "data" / "hypotheses" / "active_hypotheses.json"

NODE_ID = "HM004"
TODAY = datetime.now().strftime("%Y-%m-%d")
DEADLINE = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")   # major 层级兜底 12 个月

# ── 节点定义 ────────────────────────────────────────────────────────────

HM004 = {
    "id": NODE_ID,
    "level": "major",
    "title": "生物安全事件升级引发跨境防控收紧",
    "direction": "toward",
    "confidence": 0.35,
    "base_confidence": 0.35,
    "deadline": DEADLINE,
    "deadline_source": "level_default",
    "status": "active",
    "rationale": (
        "高致病性病原体研究机构发生事故、或出现原因不明的高致死呼吸道感染时，"
        "事件会沿「实验室安全 → 地方防控 → 国际通报 → 跨境防控」链条外溢。"
        "2026-10-02 俄伊尔库茨克防鼠疫研究所 28 岁实验员死亡、近 200 人隔离、"
        "官方口径从「鼠疫」反复为「不明原因肺炎」、WHO/美/欧相继介入，是该链条的"
        "近期实例。触发跨境防控收紧的条件是：同类事件在短期内重复出现，或单一事件"
        "出现人传人证据，导致周边国家启动入境检疫、口岸管控或旅行限制。"
    ),
    "children": [NODE_ID + "_A", NODE_ID + "_B", NODE_ID + "_C"],
    "indicators": [
        {
            "name": "WHO 生物安全/高致病性传染病类通报数",
            "source": "WHO Disease Outbreak News（DON）",
            "threshold_support": "到 2027-10-09，WHO DON 发布与实验室事故或高致病性病原体相关的通报达到 3 起及以上，表明同类事件持续发生",
            "threshold_refute": "到 2027-10-09，WHO DON 未发布任何与实验室事故或高致病性病原体相关的通报（0 起），表明事件未形成持续序列",
        },
        {
            "name": "跨境防控措施公告数",
            "source": "各国卫生/边境管理部门公开公告、WHO 国际卫生条例（IHR）通报",
            "threshold_support": "到 2027-10-09，出现 1 次及以上因生物安全/高致病性传染病事件引发的区域性跨境防控升级（入境检疫加严、口岸管控、旅行限制）",
            "threshold_refute": "到 2027-10-09，未出现任何因该类事件引发的跨境防控升级措施（0 次），表明外溢链条未启动",
        },
    ],
    "falsification_criteria": (
        "到 2027-10-09，WHO DON 未发布任何与实验室事故或高致病性病原体相关的通报（0 起），"
        "表明事件未形成持续序列；到 2027-10-09，未出现任何因该类事件引发的跨境防控升级措施"
        "（入境检疫加严、口岸管控、旅行限制 0 次），表明外溢链条未启动；"
        "到 2027-10-09，无任何国家因生物安全事件对俄或对事发地发布正式旅行/贸易限制"
    ),
    "created": TODAY,
    "evidence_log": [],
    "version": 1,
    "stale_thresholds": [],
}

HM004_A = {
    "id": NODE_ID + "_A",
    "level": "medium",
    "title": "情景A：同类事件频次上升，外溢风险实质抬升",
    "direction": "toward",
    "confidence": 0.30,
    "base_confidence": 0.30,
    "deadline": DEADLINE,
    "deadline_source": "level_default",
    "status": "active",
    "parent": NODE_ID,
    "weight": 0.5,
    "rationale": (
        "高致病性病原体研究机构在全球有数百个，生物安全等级不足或操作失误会导致"
        "重复事故。若同类事件在 12 个月内出现 3 起及以上，或出现实验室外传播证据，"
        "则构成持续序列而非孤立个案。"
    ),
    "indicators": [
        {
            "name": "高致病性病原体实验室事故报告数",
            "source": "WHO DON、各国卫生监管机构通报、学术文献",
            "threshold_support": "到 2027-10-09，公开报道的高致病性病原体实验室相关事故达到 3 起及以上",
            "threshold_refute": "到 2027-10-09，公开报道的同类事故少于 3 起（含 0 起）",
        },
    ],
    "falsification_criteria": (
        "到 2027-10-09，公开报道的高致病性病原体实验室相关事故少于 3 起，"
        "表明事件仍属孤立个案而非持续序列"
    ),
    "created": TODAY,
    "evidence_log": [],
    "version": 1,
    "stale_thresholds": [],
}

HM004_B = {
    "id": NODE_ID + "_B",
    "level": "medium",
    "title": "情景B：个案局限，防控按既有机制收敛",
    "direction": "toward",
    "confidence": 0.50,
    "base_confidence": 0.50,
    "deadline": DEADLINE,
    "deadline_source": "level_default",
    "status": "active",
    "parent": NODE_ID,
    "weight": 0.5,
    "rationale": (
        "本次事件中，密切接触者检测阴性、无二代病例、事发地已解除防疫措施，"
        "符合「个案在既有防控机制内被吸收」的特征。若后续无新增病例且无跨境措施，"
        "则事件按常规公共卫生事件收敛。"
    ),
    "indicators": [
        {
            "name": "二代病例数与跨境防控措施数",
            "source": "WHO 通报、事发国卫生部门公告、周边国家边境管理公告",
            "threshold_support": "到 2027-10-09，二代病例数为 0 且跨境防控措施为 0，事件完全收敛",
            "threshold_refute": "到 2027-10-09，出现 1 例及以上二代病例，或出现 1 次及以上跨境防控措施",
        },
    ],
    "falsification_criteria": (
        "到 2027-10-09，出现 1 例及以上二代病例，或出现 1 次及以上跨境防控措施，"
        "表明事件未被既有机制完全吸收"
    ),
    "created": TODAY,
    "evidence_log": [],
    "version": 1,
    "stale_thresholds": [],
}

HM004_C = {
    "id": NODE_ID + "_C",
    "level": "medium",
    "title": "情景C：信息不透明导致风险无法评估",
    "direction": "toward",
    "confidence": 0.45,
    "base_confidence": 0.45,
    "deadline": DEADLINE,
    "deadline_source": "level_default",
    "status": "active",
    "parent": NODE_ID,
    "weight": 0.5,
    "rationale": (
        "本次事件中官方口径从「鼠疫」改为「不明原因肺炎」、克里姆林宫称部分报道"
        "「是谎言」、WHO 要求补充信息——信息不透明本身会放大不确定性。若关键"
        "流行病学信息（病原鉴定、传播链、接触者追踪结果）长期缺失，则风险既不能"
        "被确认也不能被排除。"
    ),
    "indicators": [
        {
            "name": "关键流行病学信息公开度",
            "source": "WHO 通报、事发国卫生部门公告、国际媒体调查报道",
            "threshold_support": "到 2027-10-09，事发方仍未公布病原鉴定结果或传播链调查结论，信息缺口持续存在",
            "threshold_refute": "到 2027-10-09，事发方已公布病原鉴定与传播链调查结论，信息缺口闭合",
        },
    ],
    "falsification_criteria": (
        "到 2027-10-09，事发方已公布病原鉴定结果与传播链调查结论，"
        "表明信息缺口已闭合、风险可评估"
    ),
    "created": TODAY,
    "evidence_log": [],
    "version": 1,
    "stale_thresholds": [],
}

NEW_NODES = [HM004, HM004_A, HM004_B, HM004_C]


# ── 校验 ────────────────────────────────────────────────────────────────

def validate(nodes):
    """结构自校验：不过则返回错误列表（调用方据此放弃写入）"""
    errs = []
    import re
    for n in nodes:
        nid = n.get("id", "?")
        if not n.get("id") or not n.get("title"):
            errs.append(f"{nid}: 缺 id 或 title")
        if n.get("level") not in ("mega", "major", "medium", "small"):
            errs.append(f"{nid}: level 非法 ({n.get('level')})")
        if not isinstance(n.get("base_confidence"), (int, float)) or not (0 < n["base_confidence"] < 1):
            errs.append(f"{nid}: base_confidence 越界 ({n.get('base_confidence')})")
        if not n.get("falsification_criteria"):
            errs.append(f"{nid}: 缺 falsification_criteria")
        else:
            fc = n["falsification_criteria"]
            # 证伪判据必须含年份或数值（AGENTS.md：无法检验的表述必须拒绝）
            if not (re.search(r"20\d{2}", fc) or re.search(r"\d", fc)):
                errs.append(f"{nid}: 证伪判据无年份/数值")
        inds = n.get("indicators") or []
        if not inds:
            errs.append(f"{nid}: 无 indicators")
        for i, ind in enumerate(inds):
            if not ind.get("threshold_refute"):
                errs.append(f"{nid}: indicators[{i}] 缺 threshold_refute")
            if not ind.get("threshold_support"):
                errs.append(f"{nid}: indicators[{i}] 缺 threshold_support")
        # 父子指针一致性
        if n.get("parent") and n["parent"] not in [x["id"] for x in nodes] + [NODE_ID]:
            errs.append(f"{nid}: parent 指向不存在的节点 ({n['parent']})")
    # major 必须有 children
    if not HM004.get("children"):
        errs.append("HM004: major 节点缺 children（AGENTS.md 要求 major 有竞争面）")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印将写入的节点，不落盘")
    args = ap.parse_args()

    if not HYP_FILE.exists():
        print(f"[HM004] 假设树不存在: {HYP_FILE}")
        return 1

    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    print(f"[HM004] 当前节点数: {len(nodes)}")

    existing = {n.get("id") for n in nodes}
    if NODE_ID in existing:
        print(f"[HM004] {NODE_ID} 已存在，跳过（幂等）")
        return 0

    errs = validate(NEW_NODES)
    if errs:
        print("[HM004] 结构校验失败，放弃写入：")
        for e in errs:
            print("   -", e)
        return 1
    print(f"[HM004] 结构校验通过（{len(NEW_NODES)} 个节点）")

    if args.dry:
        print("[HM004] --dry 模式，将写入：")
        for n in NEW_NODES:
            print(f"   {n['id']} [{n['level']}] {n['title']}")
        return 0

    # 备份
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = HYP_FILE.with_name(HYP_FILE.name + f".bak_pre_hm004_{stamp}")
    shutil.copy2(HYP_FILE, bak)
    print(f"[HM004] 已备份 -> {bak.name}")

    # 追加 + 原子替换
    nodes.extend(NEW_NODES)
    tmp = HYP_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(nodes, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HYP_FILE)
    print(f"[HM004] 写入完成，节点数 {len(nodes)}（+{len(NEW_NODES)}）")
    for n in NEW_NODES:
        print(f"   + {n['id']} [{n['level']}] {n['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
