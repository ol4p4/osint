#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标事实库（可审计的检索核实层）

**要解决的问题**：`verify_hypotheses.fetch_indicator_value` 只能从 4 套写死的
映射（FRED/NBS/WorldBank/macro 快照）取值，映射里没有的指标名一律返回 None →
面板显示"当前：未知"。但**"未知"≠"世上查不到"**——SIPRI 军费、DSCA 对台军售、
日本防卫省统计都是公开发布的，只是管道没接上。实测 139 个指标里 128 个（92%）
处于这种"伪未知"。

**本层设计原则（反幻觉，源自 AGENTS.md「数值型抓取宁可缺不可错」）**：
1. 每条事实**必须**带 `source_url`（https）+ `quote`（原文片段）+ `as_of`（数据时点）。
   缺任一项拒绝写入——没有出处的数字比没有数字更糟。
2. `filled_by` 记录填写者（agent/人工）与时间，可追溯、可回滚。
3. 与 `fetch_indicator_value` 的自动抓取区分开：库里的值是**经过检索核实的**，
   不是推断的。管道优先读它（本地快照之后）。
4. 陈旧检测：`--check` 标出 as_of 超过 18 个月的条目，提醒复核。

**用法**：
    python tools/indicator_facts.py --list                 # 看已填事实
    python tools/indicator_facts.py --check                # 陈旧检测
    python tools/indicator_facts.py --add "三国军费开支同比增长率" \
        --value 6.6 --unit "%" --as-of 2025-12 \
        --source-url "https://www.sipri.org/..." --source-name SIPRI \
        --quote "China's spending rose by 7.4 per cent to $336 billion"

配套：`fetch_indicator_value` 读 `data/indicator_facts.json`（见 verify_hypotheses）。
"""
import argparse
import datetime
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS_FILE = ROOT / "data" / "indicator_facts.json"

# 陈旧阈值（月）：超过则 --check 提示复核。SIPRI 年度数据、IEA 年报等更新慢，
# 18 个月足够宽松；口径变化前的老值比"未知"更有用，但不该装作最新。
STALE_MONTHS = 18


def load_facts():
    if not FACTS_FILE.exists():
        return {}
    try:
        return (json.loads(FACTS_FILE.read_text(encoding="utf-8")) or {}).get("facts", {})
    except Exception:
        return {}


def save_facts(facts):
    FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "n": len(facts), "facts": facts}
    tmp = FACTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FACTS_FILE)


def validate(entry):
    """反幻觉闸门：没有出处与原文引证的条目一律拒收"""
    errs = []
    url = entry.get("source_url") or ""
    if not re.match(r"^https://", url):
        errs.append("source_url 必须是 https 链接")
    if not (entry.get("quote") or "").strip():
        errs.append("quote 不能为空（必须给原文片段，防止凭记忆填数）")
    if entry.get("value") is None:
        errs.append("value 不能为空")
    if not (entry.get("as_of") or "").strip():
        errs.append("as_of 不能为空（数据时点，如 2025-12）")
    if not (entry.get("source_name") or "").strip():
        errs.append("source_name 不能为空（发布机构）")
    return errs


def _months_old(as_of):
    """as_of 形如 2025 / 2025-12 / 2026-03；估算距今月数"""
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?", str(as_of or ""))
    if not m:
        return None
    y = int(m.group(1))
    mo = int(m.group(2) or 12)
    now = datetime.datetime.now()
    return (now.year - y) * 12 + (now.month - mo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", metavar="INDICATOR", help="指标名（须与假设树完全一致）")
    ap.add_argument("--value", type=float)
    ap.add_argument("--unit", default="")
    ap.add_argument("--as-of", dest="as_of", default="")
    ap.add_argument("--source-url", dest="source_url", default="")
    ap.add_argument("--source-name", dest="source_name", default="")
    ap.add_argument("--quote", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--filled-by", dest="filled_by", default="agent")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true", help="--list 输出 JSON")
    args = ap.parse_args()

    facts = load_facts()

    if args.add:
        entry = {
            "value": args.value, "unit": args.unit, "as_of": args.as_of,
            "source_url": args.source_url, "source_name": args.source_name,
            "quote": args.quote, "note": args.note,
            "filled_by": args.filled_by,
            "filled_at": datetime.datetime.now().strftime("%Y-%m-%d"),
        }
        errs = validate(entry)
        if errs:
            print("拒绝写入（反幻觉闸门）：")
            for e in errs:
                print("  - " + e)
            raise SystemExit(1)
        old = facts.get(args.add)
        facts[args.add] = entry
        save_facts(facts)
        print(f"{'更新' if old else '新增'}事实: {args.add} = {args.value}{args.unit} "
              f"({args.source_name}, {args.as_of})")
        return

    if args.check:
        if not facts:
            print("事实库为空")
            return
        print(f"事实库 {len(facts)} 条，陈旧阈值 {STALE_MONTHS} 个月：")
        stale = 0
        for k, v in sorted(facts.items()):
            mo = _months_old(v.get("as_of"))
            flag = ""
            if mo is None:
                flag = "  ⚠ 时点无法解析"
            elif mo > STALE_MONTHS:
                flag = f"  ⚠ 距今约 {mo} 个月，建议复核"
                stale += 1
            print(f"  {k} = {v.get('value')}{v.get('unit', '')} ({v.get('as_of')}){flag}")
        print(f"\n需复核: {stale}")
        return

    # 默认 --list
    if args.json:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
        return
    if not facts:
        print("事实库为空。用 --add 添加（须带 source-url / quote / as-of）。")
        return
    print(f"事实库 {len(facts)} 条：")
    for k, v in sorted(facts.items()):
        print(f"  {k} = {v.get('value')}{v.get('unit', '')} "
              f"[{v.get('as_of')}] {v.get('source_name')} — {v.get('source_url')}")


if __name__ == "__main__":
    main()
