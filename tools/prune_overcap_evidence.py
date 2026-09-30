# -*- coding: utf-8 -*-
r"""prune_overcap_evidence.py - 清理 bug 期间灌入的超标证据（2026-09-30，一次性）

**背景**：Pass 2/Pass 3 的 cap 曾是"每轮"计数而非"每日"累计，导致同一天多次
运行各灌一批。实测 9-30 单日：直接证据最多 18 条（应 ≤6）、上卷最多 110 条（应 ≤4）。
9-29 更严重（HM101 单日 481 条），JEV 日消耗从 5 万 token 暴涨到 798 万。

**清理策略**（避免浪费已花的额度）：
  - **已进 ACH 矩阵**的条目：保留。它们已被 JEV 诊断过（钱已花），
    删除只是把已付的成本丢掉，不减少支出。
  - **未进矩阵**的条目：删除。它们是白灌的（还没诊断），删掉直接省下未来的诊断费。

**排序依据**：与生产 `_sel_key` 一致（TF-IDF 优先 → relevance 降序），
即保留"最该保留的 top-N"，与 cap 语义一致。

用法：
    python tools/prune_overcap_evidence.py --dry     # 只看会删什么
    python tools/prune_overcap_evidence.py --write   # 实际写入
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"
MATRIX_FILE = BASE / "hypotheses" / "ach_matrix.json"

# cap 值**从生产代码导入**，不硬编码——首版硬编码 6/4，在 cap 提到 20 后
# 把合法灌入的 20 条误判为超标删除（2026-09-30 踩坑）。生产值是唯一事实源。
sys.path.insert(0, str(PROJECT))
try:
    from link_intel_hyp import EVIDENCE_DAILY_CAP as DIRECT_CAP
    _ROLLUP_CAP = 4   # link_intel_hyp 里是函数内局部常量，此处保持同步
except Exception:
    DIRECT_CAP = 20
    _ROLLUP_CAP = 4
ROLLUP_CAP = _ROLLUP_CAP


def evidence_key(ev):
    raw = str(ev.get("date", "")) + "|" + str(ev.get("summary", ""))[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只报告，不写盘")
    ap.add_argument("--write", action="store_true", help="实际写入")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="清理哪一天的（默认今天）")
    args = ap.parse_args()
    if not args.dry and not args.write:
        print("请指定 --dry 或 --write")
        return 1

    diag_keys = set()
    if MATRIX_FILE.exists():
        m = json.loads(MATRIX_FILE.read_text(encoding="utf-8"))
        diag_keys = {r["key"] for r in m.get("evidence", [])}

    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]

    print(f"目标日期: {args.date} | 已诊断证据 {len(diag_keys)} 条")
    print("=" * 68)

    total_drop = 0
    total_keep = 0
    for h in majors:
        log = h.get("evidence_log") or []
        today_ev = [e for e in log if str(e.get("date")) == args.date]
        if not today_ev:
            continue

        direct = [e for e in today_ev if not e.get("from_child")]
        rolled = [e for e in today_ev if e.get("from_child")]
        # 与生产排序一致：TF-IDF 优先（relevance 量纲不同这里统一按 relevance 降序）
        direct.sort(key=lambda e: -float(e.get("relevance") or 0))
        rolled.sort(key=lambda e: -float(e.get("relevance") or 0))

        drop = direct[DIRECT_CAP:] + rolled[ROLLUP_CAP:]
        keep_set = set(id(e) for e in (direct[:DIRECT_CAP] + rolled[:ROLLUP_CAP]))

        dropped_und = 0
        dropped_diag = 0
        new_log = []
        for e in log:
            if id(e) in keep_set:
                new_log.append(e)
                total_keep += 1
                continue
            if str(e.get("date")) != args.date:
                new_log.append(e)
                continue
            # 是今日超标条目
            if evidence_key(e) in diag_keys:
                new_log.append(e)   # 已诊断：保留（钱已花）
                dropped_diag += 1
            else:
                dropped_und += 1    # 未诊断：删除（省未来的钱）
        total_drop += dropped_und

        if dropped_und or dropped_diag:
            print(f"  {h['id']}: 直接 {len(direct)}→{min(len(direct),DIRECT_CAP)} "
                  f"上卷 {len(rolled)}→{min(len(rolled),ROLLUP_CAP)} | "
                  f"删未诊断 {dropped_und}，保留已诊断 {dropped_diag}")
        if args.write:
            h["evidence_log"] = new_log

    print("=" * 68)
    print(f"合计: 删除未诊断 {total_drop} 条，保留已诊断超标条目（钱已花，不浪费）")

    if args.write:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = HYP_FILE.with_suffix(f".json.bak_prune_{ts}")
        shutil.copy2(HYP_FILE, bak)
        HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {HYP_FILE.name}（备份 {bak.name}）")
    else:
        print("（--dry 模式，未写盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
