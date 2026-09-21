# -*- coding: utf-8 -*-
r"""backfill_diag_source.py - 存量判定回填来源标记（2026-09-21，跑一次即弃）

背景：ach_matrix.json 的 437 行判定此前无 model/diagnosed_at 字段，
是"静态快照"而非可追溯过程。本脚本一次性回填：
  - model: 存量判定全部来自 mimo（JEV 接入后从未在生产跑过）
  - diagnosed_at: 从 git 历史推断（取该行首次出现的提交日期），无法推断则留空
  - schema: "legacy"（无 conf 字段=旧口径 lr 自报）/"v2"（有 conf=新口径代码推导）

幂等：已有 model 字段的行跳过。

用法：
    python tools/backfill_diag_source.py --dry    # 预览
    python tools/backfill_diag_source.py          # 执行
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
MATRIX = BASE / "hypotheses" / "ach_matrix.json"


def git_first_seen(key):
    """从 git 历史推断该 key 首次出现的时间（返回 YYYY-MM-DD 或空串）。

    用 git log -S 搜 key 字符串在该文件历史中的首次出现。
    成本高（每 key 一次 git 调用），故加缓存 + 失败静默。
    """
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=%ai", "-S", key, "--",
             "data/hypotheses/ach_matrix.json"],
            cwd=str(PROJECT), capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0][:10]
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-git", action="store_true", help="跳过 git 推断（快，但 diagnosed_at 留空）")
    args = ap.parse_args()

    d = json.loads(MATRIX.read_text(encoding="utf-8"))
    ev = d.get("evidence") or []
    print(f"[BACKFILL] 矩阵 {len(ev)} 行")

    todo = [e for e in ev if "model" not in e]
    print(f"[BACKFILL] 待回填 {len(todo)} 行（已有 model 的 {len(ev) - len(todo)} 行跳过）")
    if not todo:
        print("[BACKFILL] 无需回填")
        return

    # 统计口径分布
    v2 = sum(1 for e in todo if any("conf" in dd for dd in (e.get("diagnosis") or {}).values()))
    print(f"[BACKFILL] 口径: v2(有conf) {v2} 行 / legacy(无conf) {len(todo) - v2} 行")
    print("[BACKFILL] 来源: 全部标记为 mimo（JEV 接入后从未在生产跑过）")
    if args.dry:
        for e in todo[:5]:
            print("   -", e.get("key"), e.get("summary", "")[:50])
        print(f"[BACKFILL] --dry 预览结束（共 {len(todo)} 行待处理）")
        return

    if not args.no_git:
        print("[BACKFILL] 从 git 历史推断 diagnosed_at（较慢，逐 key 查询）…")

    done = 0
    for e in todo:
        e["model"] = "mimo"
        if not e.get("diagnosed_at"):
            e["diagnosed_at"] = "" if args.no_git else git_first_seen(e.get("key", ""))
        # 口径标记：有 conf 字段 = v2（LR 代码推导），否则 legacy（模型自报 lr）
        has_conf = any("conf" in dd for dd in (e.get("diagnosis") or {}).values())
        e["schema"] = "v2" if has_conf else "legacy"
        done += 1
        if done % 50 == 0:
            print(f"   已处理 {done}/{len(todo)}")

    if args.dry:
        return
    d["updated_at"] = d.get("updated_at", "")
    MATRIX.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[BACKFILL] 完成 {done} 行 -> {MATRIX}")

    # 统计
    from collections import Counter
    c = Counter(e.get("schema") for e in ev)
    m = Counter(e.get("model") for e in ev)
    dated = sum(1 for e in ev if e.get("diagnosed_at"))
    print(f"[BACKFILL] schema 分布: {dict(c)}")
    print(f"[BACKFILL] model 分布: {dict(m)}")
    print(f"[BACKFILL] 有 diagnosed_at 的: {dated}/{len(ev)}")


if __name__ == "__main__":
    main()
