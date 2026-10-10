#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叙述式阈值的 AI 判定（展示层，不改置信度）

背景：verify_hypotheses 的 parse_threshold 只认简式数值阈值（'>2.5%'、'<200bp'），
而绝大多数指标阈值是叙述式（"到 2027 年，三国军费较 2026 年增长 15% 以上"），
数值比较不适用 → 标 needs_ai → 由周循环 AI 裁判兜底。但周循环裁判只在
deadline 到期（最早 2026-12-05）才触发，导致当前已取到真实值的指标长期无判定
——面板上只能显示"待AI裁判"，等于永远不判。

本工具把这一步提前：对**有真实抓取值**（data_source 非空）的 needs_ai 指标，
批量送 AI 判定"当前值 vs 叙述式阈值"，结果落 indicator_assessments.json
并写回 ind["ai_assessment"]。

**不写 confidence**（AGENTS.md：置信度只能由判定层——ACH 贝叶斯后验与周循环
AI 裁判——写入；指标级预判不足以改假设级置信度）。本工具纯展示。

节流：按日（data/.assess_last_run），供 refresh.py 调用；--force 绕过。
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "local"))

HYP_FILE = ROOT / "data" / "hypotheses" / "active_hypotheses.json"
OUT_FILE = ROOT / "data" / "indicator_assessments.json"
THROTTLE = ROOT / "data" / ".assess_last_run"
BATCH = 10

SYSTEM = (
    "你是指标判定助手。给定指标的当前值、支持阈值、证伪阈值，判断当前值是否"
    "满足阈值。只依据给定数值与阈值文本推理，不引入外部知识、不猜测缺失数据。"
    "阈值若含具体数字，严格按数字比较；若为年度目标（如'到2027年'），当前值"
    "尚未到期则判 uncertain。返回严格 JSON 数组，每项 "
    '{"index":编号,"verdict":"met|unmet|uncertain","reason":"一句话依据"}。'
)


def _load_analyzer():
    import yaml
    from analyze import MacroAnalyzer
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return MacroAnalyzer(cfg, "", None)


def collect_targets(hyps):
    """有真实抓取值、但阈值是叙述式（needs_ai）的指标"""
    out = []
    for h in hyps:
        for ind in (h.get("indicators") or []):
            if ind.get("verify_status") == "needs_ai" and ind.get("data_source"):
                out.append((h, ind))
    return out


def _build_prompt(items):
    lines = []
    for i, (h, ind) in enumerate(items):
        lines.append(
            f"{i}. 指标「{ind.get('name')}」（所属假设：{h.get('title', '')}）\n"
            f"   当前值：{ind.get('current_value')}（来源：{ind.get('data_source')}，"
            f"{ind.get('last_updated') or '日期未标'}）\n"
            f"   支持阈值：{ind.get('threshold_support') or '未给'}\n"
            f"   证伪阈值：{ind.get('threshold_refute') or '未给'}"
        )
    return "请逐条判定下列指标（返回 JSON 数组）：\n\n" + "\n".join(lines)


def _salvage_objects(text):
    """括号配对状态机逐条抢救（截断/围栏/多余文本都能取出完整对象）"""
    text = re.sub(r"```json\s*", "", text or "")
    text = re.sub(r"```", "", text)
    start = text.find("[")
    if start < 0:
        return []
    out, depth, cur_start, in_str, esc = [], 0, None, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                cur_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and cur_start is not None:
                try:
                    out.append(json.loads(text[cur_start:i + 1]))
                except Exception:
                    pass
                cur_start = None
        elif ch == "]" and depth == 0:
            break
    return out


def assess(items, analyzer):
    """批量判定；返回 {index: {verdict, reason}}"""
    results = {}
    for s in range(0, len(items), BATCH):
        chunk = items[s:s + BATCH]
        try:
            raw = analyzer._call_api(SYSTEM, _build_prompt(chunk))
        except Exception as e:
            print(f"  [assess] 批次 {s} AI 调用失败: {e}")
            continue
        for obj in _salvage_objects(raw):
            try:
                idx = int(obj.get("index"))
            except (TypeError, ValueError):
                continue
            v = str(obj.get("verdict") or "").strip().lower()
            if v not in ("met", "unmet", "uncertain"):
                v = "uncertain"
            results[s + idx] = {"verdict": v, "reason": str(obj.get("reason") or "")[:200]}
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="绕过日节流")
    ap.add_argument("--dry", action="store_true", help="只看目标，不调 AI")
    args = ap.parse_args()

    if not HYP_FILE.exists():
        print("假设树不存在，跳过")
        return

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if not args.force and THROTTLE.exists():
        if THROTTLE.read_text(encoding="utf-8").strip() == today:
            print(f"今日已判定（{today}），跳过。--force 强制重跑")
            return

    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    targets = collect_targets(hyps)
    print(f"待判定指标（needs_ai 且有真实抓取值）: {len(targets)}")
    for h, ind in targets:
        print(f"  {ind.get('name')} = {ind.get('current_value')} [{h.get('level')}]")
    if not targets:
        OUT_FILE.write_text(json.dumps({"assessed_at": today, "n": 0, "items": []},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        THROTTLE.write_text(today, encoding="utf-8")
        return
    if args.dry:
        return

    analyzer = _load_analyzer()
    results = assess(targets, analyzer)

    out_items = []
    for i, (h, ind) in enumerate(targets):
        r = results.get(i)
        if not r:
            continue
        out_items.append({"hyp_id": h.get("id"), "hyp_title": h.get("title"),
                          "indicator": ind.get("name"), "current_value": ind.get("current_value"),
                          "source": ind.get("data_source"), "assessed_at": today, **r})
        print(f"  {ind.get('name')}: {r['verdict']} — {r['reason'][:60]}")

    # **不回写假设树**（2026-10-10 设计决策）：active_hypotheses.json 已有
    # link/verify/ach/probe 四个写入方，再加一个只会扩大竞态面。判定结果是
    # 展示层产物，由 gen_dashboard 在渲染时按 hyp_id+indicator 合并即可。
    OUT_FILE.write_text(json.dumps({"assessed_at": today, "n": len(out_items),
                                    "items": out_items}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    THROTTLE.write_text(today, encoding="utf-8")
    print(f"\n判定完成: {len(out_items)} 项 → {OUT_FILE.name}")
    print("注意：本工具不改 confidence、不回写假设树（展示层，供仪表盘合并）")


if __name__ == "__main__":
    main()
