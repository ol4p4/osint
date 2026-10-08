# -*- coding: utf-8 -*-
r"""probe_mega.py - 探针假设的情报信号测量（2026-09-28 新增）

**背景**：假设树里有两类节点，此前混在一起处理，导致结论失真。

  - **分析类**（8 major）：从观点分解而来，靠证据累积 + ACH 贝叶斯更新。
    进 ach_matrix，走 bayesian_update 出后验。这是现有的正经链路。
  - **探针类**（2 mega）：用户主动问系统「这个可能性多大」的问题，
    如「三战在5年内爆发」「全球性经济危机」。它们**不在 ACH 矩阵里**
    （ach_daily_batch 只取 level=='major'），但历史上被
    link_intel_hyp 的涨跌词规则污染过——先验 0.05 被推到 0.94，
    贡献证据却是「大熊猫抵达美国」这类无关条目。

**本工具做什么**：用 JEV 的 Noul 原语逐条问「这条情报是否提升了该议题的
风险/概率」，把答案聚合成一个**信号强度**，供人工判读或作为探针节点的
参考读数。**它不写 confidence**——探针的概率是用户的问题，不是系统的结论；
工具只提供「当前情报语料里，支持该议题升级的信号占比」这个客观输入。

**为什么不用关键词计数**：涨跌词衡量的是行情语气，不是议题相关性。
实测噪声（铁路客运/原油行情/A股开盘）在 Noul 下得 0.01~0.04，
真信号（实弹演习）得 0.86——两者有 20 倍间隔（见 jev_client 文件头）。

用法：
    python tools/probe_mega.py --dry                 # 只看会跑什么，不调 API
    python tools/probe_mega.py --limit 200           # 小批实测（推荐先做）
    python tools/probe_mega.py --days 7              # 近 7 天语料（默认 3 天）
    python tools/probe_mega.py --write               # 把读数写入探针节点（只写 probe_reading）

落盘：data/hypotheses/probe_readings.json（每次运行追加，保留历史）
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"
READINGS_FILE = BASE / "hypotheses" / "probe_readings.json"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))

# 门控阈值：低于此值视为「与议题无关」，不计入信号。
# 依据见 jev_client.gate_and_diagnose 文件头（噪声上界 0.04 / 真信号下界 0.14）。
# 2026-09-28 实测校准：0.08 会把「日内请重点关注」「昨日今晨新闻汇总」这类
# 汇总条目算成信号（实测 18/40 命中，其中 3 条是汇总类误判）。
# 改双阈值：weak 只用于「提及」（0.08），strong 用于「实质信号」（0.20）。
# 实质信号的实测样本：霍尔木兹/美伊冲突 0.34~0.60；噪声 0.01~0.07。
GATE_THRESHOLD = 0.08
STRONG_THRESHOLD = 0.20


def load_probe_nodes():
    """探针节点 = level=='mega'。它们的概率是用户的问题，不进 ACH 矩阵。"""
    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    return [n for n in nodes if n.get("level") == "mega" and n.get("status") == "active"]


def load_intel(days=3, limit=None):
    """近 N 天的情报（按 published_at 过滤），按 final_score 降序取前 limit 条。

    为什么按分数排序：探针要测的是「语料里的信号强度」，低分噪声条目
    （广告、纯行情播报）会稀释信号。取高分条目是采样，不是筛选结论——
    采样口径在报告里显式写出，保证可复核。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lo = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    hi = now.strftime("%Y-%m-%d")

    files = sorted(BASE.glob("intel_2*.jsonl"), reverse=True)[:days + 2]
    seen, items = set(), []
    for f in files:
        if "raw" in f.name or "final" in f.name:
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = it.get("id")
            if not iid or iid in seen:
                continue
            pub = str(it.get("published_at") or "")[:10]
            if not (lo <= pub <= hi):
                continue
            seen.add(iid)
            items.append(it)

    def score_key(it):
        try:
            return (float(it.get("final_score") or 0), str(it.get("published_at") or ""))
        except (TypeError, ValueError):
            return (0.0, str(it.get("published_at") or ""))

    items.sort(key=score_key, reverse=True)
    if limit:
        items = items[:limit]
    return items, lo, hi


def build_probe_questions(probes):
    """每条情报 → 每个探针一个 Noul 问题。

    措辞要点（2026-09-28 两轮实测校准）：

    1. 用「是否提升风险」而非「是否与预期一致」——后者会被字面理解为
       「这条消息是否支持该假设成立」，导致市场正常运行被判成反对
       （沿用 gate_and_diagnose 的实测结论）。

    2. **必须把节点自己的判定定义写进问题**。首版只写议题名（"全球性经济危机"），
       实测把霍尔木兹油价、美债收益率这类**能源地缘事件**全算成危机信号
       （120 条里 60 条命中 0.2+，抽查发现大部分是油价新闻）。
       根因：「经济危机」字面太宽，任何财经波动都能沾边。
       现在把 rationale/falsification_criteria 里的**具体定义**写进去
       （GDP 连续 2 年负增长 / 股指回撤 >40%），JEV 据此判断才有边界。

    3. 显式声明「间接关联不计入」——地缘冲突是危机的**触发因素**而非危机本身，
       要让模型区分「向该定义推进」与「相关但未推进」。
    """
    qs = {}
    for p in probes:
        title = str(p.get("title", ""))
        rationale = str(p.get("rationale") or "").strip()
        falsify = str(p.get("falsification_criteria") or "").strip()
        inds = [str(i.get("name")) for i in (p.get("indicators") or [])
                if isinstance(i, dict) and i.get("name")]

        parts = [
            "这条情报的内容是否**实质推动**了「" + title + "」这一情景向发生靠近？",
        ]
        if rationale:
            parts.append("该情景的定义是：" + rationale[:200])
        if falsify:
            parts.append("其证伪条件是：" + falsify[:150])
        if inds:
            parts.append("其观测指标为：" + "、".join(inds[:3]) + "。")
        parts.append(
            "判断标准：只有当情报内容直接指向上述定义/指标的变化时才给高概率；"
            "仅与议题主题相关（如地缘冲突、油价波动、市场情绪）但未推动该定义的，"
            "属于间接关联，应给中低概率；完全无关给低概率。"
        )
        qs["p_" + p["id"]] = {"type": "noul", "instructions": " ".join(parts)}
    return qs


def aggregate(readings):
    """把逐条 Noul 概率聚合成信号强度。

    用「超过阈值的条目占比」而非平均概率——平均值会被大量 0.01~0.04
    的无关条目稀释成无意义的小数；占比直接回答「语料里有百分之几在谈这件事」。

    双档口径（2026-09-28 实测校准）：
      mention_ratio  提及率：gate >= 0.08（含汇总类条目的宽口径）
      signal_ratio   信号率：gate >= 0.20（实质信号，用于判读）
    只看 signal_ratio——mention 口径会把「今日重点关注」这类汇总条目算进来。
    """
    if not readings:
        return {}
    out = {}
    ids = {r["probe_id"] for r in readings}
    for pid in ids:
        vals = [r["gate"] for r in readings if r["probe_id"] == pid]
        mention = [v for v in vals if v >= GATE_THRESHOLD]
        strong = [v for v in vals if v >= STRONG_THRESHOLD]
        out[pid] = {
            "samples": len(vals),
            "mention_count": len(mention),
            "mention_ratio": round(len(mention) / len(vals), 4),
            "signal_count": len(strong),
            "signal_ratio": round(len(strong) / len(vals), 4),
            "max_gate": round(max(vals), 3) if vals else 0.0,
            "mean_gate": round(sum(vals) / len(vals), 4) if vals else 0.0,
            "strong_signals": sorted((round(v, 3) for v in strong), reverse=True)[:10],
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="取样语料的天数窗口")
    ap.add_argument("--limit", type=int, default=0, help="最多取多少条情报（0=不限制）")
    ap.add_argument("--dry", action="store_true", help="只统计，不调 API")
    ap.add_argument("--write", action="store_true",
                    help="把读数写回探针节点的 probe_reading 字段（不写 confidence）")
    args = ap.parse_args()

    probes = load_probe_nodes()
    if not probes:
        print("[PROBE] 无探针节点（level==mega）——跳过")
        return
    print(f"[PROBE] 探针节点 {len(probes)} 个:")
    for p in probes:
        print(f"         {p['id']:32s} {p.get('title')}  (先验 {p.get('base_confidence')})")

    limit = args.limit or None
    items, lo, hi = load_intel(days=args.days, limit=limit)
    print(f"[PROBE] 语料窗口 {lo} ~ {hi}，取 {len(items)} 条（按 final_score 降序）")
    if args.dry or not items:
        return

    from jev_client import JevClient
    jev = JevClient(caller="probe_mega")
    if not jev.available:
        print("[PROBE] JEV 无可用通道——退出（探针依赖 JEV Noul）")
        return
    print("[PROBE] 决策层: " + jev.describe())

    questions = build_probe_questions(probes)
    readings = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        text = (it.get("cn_title") or it.get("title") or "")[:300]
        if not text:
            continue
        try:
            d = jev.systemone(text, questions, timeout=60)
        except Exception as ex:
            print(f"[PROBE] {i}/{len(items)} 调用失败: {str(ex)[:80]}")
            continue
        answers = d.get("answers") or {}
        for p in probes:
            a = answers.get("p_" + p["id"]) or {}
            gate = float(a.get("noul", 0.0))
            readings.append({
                "probe_id": p["id"], "gate": round(gate, 4),
                "intel_id": it.get("id"), "date": str(it.get("published_at") or "")[:10],
                "title": text[:80],
            })
        if i % 50 == 0:
            el = time.time() - t0
            print(f"[PROBE] {i}/{len(items)} 条，{el:.0f}s，约 {el / i:.2f}s/条")

    stats = aggregate(readings)
    print("\n" + "=" * 62)
    print("探针读数（signal_ratio = 语料中实质涉及该议题的条目比例）")
    print("=" * 62)
    for p in probes:
        s = stats.get(p["id"])
        if not s:
            continue
        print(f"\n{p.get('title')}  [{p['id']}]")
        print(f"  先验（用户设定）: {p.get('base_confidence')}")
        print(f"  样本: {s['samples']} 条")
        print(f"  实质信号: {s['signal_count']} 条 | 信号率 {s['signal_ratio'] * 100:.2f}%"
              f"   <- 判读用这个")
        print(f"  提及（宽口径）: {s['mention_count']} 条 | 提及率 {s['mention_ratio'] * 100:.2f}%")
        print(f"  gate 均值 {s['mean_gate']} | 最大 {s['max_gate']}")
        if s["strong_signals"]:
            print(f"  最强信号 gate 值: {s['strong_signals']}")

    # 落盘（追加式，保留历史）
    history = []
    if READINGS_FILE.exists():
        try:
            history = json.loads(READINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    history.append({
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "window": f"{lo} ~ {hi}", "samples": len(items),
        "gate_threshold": GATE_THRESHOLD, "strong_threshold": STRONG_THRESHOLD,
        "stats": stats,
    })
    READINGS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[PROBE] 读数已落盘: {READINGS_FILE}（累计 {len(history)} 次）")

    if args.write:
        nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
        now_s = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for n in nodes:
            if n.get("id") in stats:
                n["probe_reading"] = dict(stats[n["id"]], measured_at=now_s)
        HYP_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[PROBE] 读数已写入探针节点 probe_reading 字段（confidence 未改动）")


if __name__ == "__main__":
    main()
