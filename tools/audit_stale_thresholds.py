# -*- coding: utf-8 -*-
r"""audit_stale_thresholds.py - 假设判据时效审计（2026-09-21）

**背景**：`_decompose_view` 的 prompt 原先未告知模型"今天是几号"，AI 只能凭
训练数据的时间感写年份，实测产出的证伪判据大量指向 2023-2025（已过期），
与代码计算的 due_date（2027-2028）脱节。

**为什么先审计不直接重写**：过期判据有两种成因，处置方式不同：
  1. 时间基准错（AI 猜的年份）→ 可重写，且应当重写
  2. 假设本身已到期/已失效   → 不该重写，该走验证或归档
不区分就批量重写，会把"本该被验证的假设"永久推迟。

用法：
    python tools/audit_stale_thresholds.py              # 打印报告
    python tools/audit_stale_thresholds.py --json       # 机器可读
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"


def years_in(text):
    return sorted({int(y) for y in re.findall(r"(20[1-9]\d)", str(text or ""))})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    now_year = datetime.now(timezone.utc).year

    rows = []
    for h in hyps:
        fc = h.get("falsification_criteria") or ""
        ys = years_in(fc)
        due = (h.get("due_date") or h.get("deadline") or "")[:10]
        due_year = years_in(due)[0] if years_in(due) else None
        stale = [y for y in ys if y < now_year]
        rows.append({
            "id": h.get("id"), "level": h.get("level"), "title": (h.get("title") or "")[:40],
            "due_date": due, "due_year": due_year,
            "threshold_years": ys, "stale_years": stale,
            "n_indicators": len(h.get("indicators") or []),
            "n_evidence": len(h.get("evidence_log") or []),
            "has_stale": bool(stale),
        })

    stale_rows = [r for r in rows if r["has_stale"]]
    if args.json:
        print(json.dumps({"current_year": now_year, "total": len(rows),
                          "stale": len(stale_rows), "rows": rows},
                         ensure_ascii=False, indent=1))
        return

    print(f"假设判据时效审计（当前 {now_year} 年）")
    print(f"  节点总数: {len(rows)}")
    print(f"  判据含过期年份: {len(stale_rows)} ({len(stale_rows) * 100 // max(len(rows), 1)}%)")
    print()
    print("按层级:")
    for lv in ("mega", "major", "medium", "small", None):
        sub = [r for r in rows if r["level"] == lv]
        if not sub:
            continue
        s = sum(1 for r in sub if r["has_stale"])
        print(f"  {str(lv or '未标'):8s} {s}/{len(sub)} 过期")
    print()
    print("--- 过期节点明细（按 due_date 排序）---")
    print(f"{'层级':8s} {'due_date':12s} {'判据年份':22s} {'标题':30s}")
    print("-" * 76)
    for r in sorted(stale_rows, key=lambda x: x["due_date"] or "9"):
        ys = ",".join(str(y) for y in r["stale_years"])
        print(f"{str(r['level'] or '?'):8s} {r['due_date']:12s} {ys:22s} {r['title']:30s}")
    print()
    print("--- 处置建议 ---")
    n_future = sum(1 for r in stale_rows if r["due_year"] and r["due_year"] > now_year)
    n_past = sum(1 for r in stale_rows if r["due_year"] and r["due_year"] <= now_year)
    print(f"  due_date 仍在未来（{now_year + 1}+）: {n_future} 个 → **可重写判据**"
          f"（时间基准错，假设本身有效）")
    print(f"  due_date 已到/已过: {n_past} 个 → **走验证流程**（不该重写，"
          f"否则把该验证的假设永久推迟）")
    print()
    print("  重写入口：在假设生成时已加时间闸门（_stale_years），")
    print("            存量重写需单独脚本（建议先人工过一遍上面的明细）")


if __name__ == "__main__":
    main()
