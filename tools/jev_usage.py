# -*- coding: utf-8 -*-
r"""jev_usage.py - JEV 用量账本（2026-09-21）

背景：TypeSafe API **没有用量查询端点**（实测 /v1/usage、/v1/account、/v1/me、
/v1/credits 全部 404，只有 /v1/models 可用）。额度只能靠本地记账追踪。

每次 API 响应都带 usage 字段（input_tokens / output_tokens），据此累计：
    data/jev_usage.json   累计总量 + 按日/按调用方明细

定价（官方）：输入 $0.042/Mtok，**输出免费**。所以成本只看 input_tokens。

用法：
    python tools/jev_usage.py              # 查看累计用量
    python tools/jev_usage.py --json       # 机器可读
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
LEDGER = BASE / "jev_usage.json"

PRICE_INPUT_PER_MTOK = 0.042   # 美元 / 百万输入 token（官方定价）
PRICE_OUTPUT_PER_MTOK = 0.0    # 输出免费


def _load():
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"total": {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0},
            "by_day": {}, "by_caller": {}}


def record_usage(input_tokens, output_tokens, caller="unknown", calls=1):
    """记一笔用量（幂等累加）。失败静默——记账不该影响主流程。"""
    try:
        d = _load()
        it, ot = int(input_tokens or 0), int(output_tokens or 0)
        cost = it / 1e6 * PRICE_INPUT_PER_MTOK + ot / 1e6 * PRICE_OUTPUT_PER_MTOK
        t = d.setdefault("total", {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0})
        t["input_tokens"] += it
        t["output_tokens"] += ot
        t["calls"] += int(calls or 1)
        t["cost_usd"] = round(t["cost_usd"] + cost, 6)
        day = time.strftime("%Y-%m-%d")
        bd = d.setdefault("by_day", {}).setdefault(day, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        bd["input_tokens"] += it
        bd["output_tokens"] += ot
        bd["calls"] += int(calls or 1)
        bc = d.setdefault("by_caller", {}).setdefault(caller, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        bc["input_tokens"] += it
        bc["output_tokens"] += ot
        bc["calls"] += int(calls or 1)
        d["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return d
    except Exception:
        return None


def summary():
    return _load()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    d = _load()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    t = d.get("total") or {}
    print("JEV 用量账本 (" + str(LEDGER) + ")")
    print("  更新于:", d.get("updated_at", "—"))
    print()
    print(f"  累计调用: {t.get('calls', 0)} 次")
    print(f"  输入 token: {t.get('input_tokens', 0):,}")
    print(f"  输出 token: {t.get('output_tokens', 0):,}  (不计费)")
    print(f"  累计成本: ${t.get('cost_usd', 0.0):.6f}")
    print()
    print(f"  定价参考: 输入 ${PRICE_INPUT_PER_MTOK}/Mtok, 输出免费")
    print(f"  10 亿 token = $42（官方说法）")
    it = t.get("input_tokens", 0)
    if it:
        print(f"  当前用量占 10 亿 token 的 {it / 1e9 * 100:.4f}%")
    by_day = d.get("by_day") or {}
    if by_day:
        print()
        print("  按日:")
        for day in sorted(by_day)[-14:]:
            v = by_day[day]
            print(f"    {day}  {v['calls']:4d} 次  in={v['input_tokens']:>9,}")
    by_caller = d.get("by_caller") or {}
    if by_caller:
        print()
        print("  按调用方:")
        for c, v in sorted(by_caller.items(), key=lambda kv: -kv[1]["input_tokens"]):
            print(f"    {c:22s} {v['calls']:4d} 次  in={v['input_tokens']:>9,}")


if __name__ == "__main__":
    main()
