# -*- coding: utf-8 -*-
r"""recover_lost_impact.py - 恢复被预填覆盖丢失的 impact 字段（2026-10-14）

## 背景

translate_local 的全量预填（同日上线）暴露了 write_back_to_jsonl 的隐藏缺陷：
更新字典无条件包含 impact/cn_summary（缺失用空串），而预填条目不带这两个
字段 → 把「已研判但 cn_title 为空」条目的 impact 覆盖空。实测一次损伤
2,398 条（有 impact_level 的条目 impact 变空串）。

修复已在 translate_local（只写非空字段）。本脚本恢复已损伤的数据：
从所有历史快照（cache/、仓库根 CI 文件、raw/final）扫回 impact 原值。

## 用法

  python tools/recover_lost_impact.py --dry     # 只统计可恢复数
  python tools/recover_lost_impact.py --write   # 执行恢复（原子写回）

## 风险

只改 data/intel_2*.jsonl 中受损条目的 impact/cn_summary 两个字段，
仅在恢复源有非空值时写；无损操作（不回退其他字段）。
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(r"D:\osint\data")
ROOT = Path(r"D:\osint")


def _scan_impact_sources(damaged_ids):
    """从所有非活跃文件扫 impact 原值。返回 {id: {impact, cn_summary}}。"""
    sources = []
    for pat, base in (("intel_2*.jsonl", ROOT),          # 仓库根 CI 文件
                      ("intel_raw_*.jsonl", BASE),
                      ("intel_final_*.jsonl", BASE),
                      ("*.jsonl", BASE / "cache"),
                      ("*.bak*", BASE)):                  # 2026-10-14: bak 备份也有原值
        sources.extend(base.glob(pat))

    found = {}
    for f in sources:
        # 跳过活跃文件本体（受损所在）：仅当纯 .jsonl 且形如 intel_2*.jsonl。
        # 踩坑：首版条件写成 startswith("intel_20")，把 .bak_rescore_* 备份
        # 也一起跳过了（它们同前缀）→ 361 条可恢复被漏掉。
        if f.name.startswith("intel_2") and f.suffix == ".jsonl" and f.parent == BASE:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            iid = d.get("id")
            if iid in damaged_ids and (d.get("impact") or "").strip():
                found.setdefault(iid, {})
                found[iid]["impact"] = d["impact"]
                if (d.get("cn_summary") or "").strip():
                    found[iid]["cn_summary"] = d["cn_summary"]
    return found


def main():
    write = "--write" in sys.argv
    dry = "--dry" in sys.argv or not write

    files = sorted(BASE.glob("intel_2*.jsonl"))
    damaged = {}   # id -> (file, impact_level)
    for f in files:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("impact_level") and not (d.get("impact") or "").strip():
                damaged[d.get("id")] = f

    print(f"[recover] 受损条目 {len(damaged)} 条（有 impact_level 但 impact 空）")
    if not damaged:
        return

    found = _scan_impact_sources(set(damaged))
    print(f"[recover] 可从历史快照恢复 {len(found)} 条"
          f"（{len(damaged) - len(found)} 条无恢复源）")
    if dry:
        print("[recover] --dry 模式，未写盘。加 --write 执行。")
        return

    # 按文件分组写回（原子替换）
    from collections import defaultdict
    by_file = defaultdict(dict)
    for iid, val in found.items():
        by_file[damaged[iid]][iid] = val

    total = 0
    for f, vals in by_file.items():
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        n = 0
        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue
            try:
                d = json.loads(line_s)
            except ValueError:
                out.append(line)
                continue
            iid = d.get("id")
            if iid in vals and not (d.get("impact") or "").strip():
                d.update(vals[iid])
                n += 1
            out.append(json.dumps(d, ensure_ascii=False))
        if n:
            tmp = f.with_suffix(f.suffix + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            os.replace(tmp, f)
            total += n
            print(f"[recover] {f.name}: 恢复 {n} 条")
    print(f"[recover] 完成，共恢复 {total} 条")


if __name__ == "__main__":
    main()
