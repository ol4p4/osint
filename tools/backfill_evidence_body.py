# -*- coding: utf-8 -*-
r"""backfill_evidence_body.py - 从原始情报回填证据的 body 字段（2026-09-29）

**问题**：`link_intel_hyp.py` 在 2026-09-28 才加 `body` 字段（存 content_preview），
存量 18,178 条证据里只有 222 条（1%）有正文。而 JEV 判定依赖正文来区分
「A股军工板块拉升」（市场反应）与「解放军台海演习」（军事行动）——两条都含
"军工/台海"字面，只看标题必然误判。

**做法**：evidence_log 里每条都存了 `intel_ids`（原始情报 id）。扫描全库
intel_*.jsonl 建 id→content_preview 索引，回填缺失的 body。

**不覆盖已有 body**（新证据的 body 来自采集时的现场数据，比事后反查更准）。

用法：
    python tools/backfill_evidence_body.py --dry     # 只看能回填多少
    python tools/backfill_evidence_body.py           # 回填并写盘
"""
import argparse
import glob
import json
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"


def build_intel_index():
    """id → 正文片段（优先 cn_summary，退 content_preview，再退 content）"""
    idx = {}
    files = sorted(glob.glob(str(BASE / "intel_2*.jsonl")))
    for f in files:
        name = Path(f).name
        if "raw" in name or "final" in name:
            continue
        try:
            text = Path(f).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = d.get("id")
            if not iid or iid in idx:
                continue
            body = (d.get("cn_summary") or d.get("content_preview")
                    or d.get("content") or "")
            if body:
                idx[iid] = str(body)[:400]
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计，不写盘")
    args = ap.parse_args()

    print("[BODY] 建原始情报索引...")
    idx = build_intel_index()
    print(f"[BODY] 索引 {len(idx)} 条情报")

    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    total = already = filled = miss = 0
    for h in nodes:
        for e in (h.get("evidence_log") or []):
            if not isinstance(e, dict):
                continue
            total += 1
            if e.get("body"):
                already += 1
                continue
            got = ""
            for iid in (e.get("intel_ids") or []):
                if iid in idx:
                    got = idx[iid]
                    break
            if got:
                if not args.dry:
                    e["body"] = got
                filled += 1
            else:
                miss += 1

    print(f"[BODY] evidence_log 共 {total} 条")
    print(f"  已有 body : {already}")
    print(f"  可回填    : {filled}")
    print(f"  查不到原文: {miss}（原始情报已过期/未采集）")

    if args.dry:
        print("[BODY] --dry 模式，未写盘")
        return
    HYP_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[BODY] 已写盘：{HYP_FILE}")


if __name__ == "__main__":
    main()
