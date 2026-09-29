# -*- coding: utf-8 -*-
r"""reclass_meta_hypotheses.py - 元命题重分类（2026-09-29 一次性）

把两个「元命题」从 major 降为 mega，移出 ACH 诊断范围。

**为什么**：`hyp_e2b46e32`（东亚三国现代化进程趋同）与 `hyp_d26158d3`
（中国社保走韩国老路）覆盖多年/多领域的总判断，与探针节点（三战/经济危机）
同级。实测 ACH 诊断为 0 条 C/I——JEV 面对「这条情报是否改变该元命题的
可能性」无从下手，因为元命题不是可被单条情报推动的竞争假设。

降为 mega 后与探针同级，走 `tools/probe_mega.py` 测情报信号占比，
而非硬塞进 ACH 累积证据。

用法：
    python tools/reclass_meta_hypotheses.py --dry
    python tools/reclass_meta_hypotheses.py
"""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\osint\data")
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"
MATRIX_FILE = BASE / "hypotheses" / "ach_matrix.json"

TARGETS = {
    "hyp_e2b46e32": "东亚三国现代化进程趋同",
    "hyp_d26158d3": "中国社保走韩国老路",
}

NOTE = ("2026-09-29 元命题重分类：覆盖多年/多领域的总判断，实测 ACH 诊断 0 条 C/I"
        "（JEV 无从判断「这条情报是否改变该元命题的可能性」）。改为与探针同级，"
        "走 probe_mega 测情报信号占比，不再进 ACH 矩阵。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    changed = []
    for n in nodes:
        if n.get("id") in TARGETS and n.get("level") == "major":
            changed.append((n["id"], n.get("title")))
            if not args.dry:
                n["level"] = "mega"
                n["reclass_note"] = NOTE

    print("[RECLASS] 待调整节点:")
    for i, t in changed:
        print(f"  {i:16s} major -> mega  {t}")

    if args.dry:
        print("[RECLASS] --dry 模式，未写盘")
        return
    if not changed:
        print("[RECLASS] 无需调整")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in (HYP_FILE, MATRIX_FILE):
        shutil.copy2(f, f.with_name(f.name + ".bak_reclass_" + ts))

    HYP_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[RECLASS] 已写盘 {HYP_FILE}")

    # 矩阵里的 8 个假设对象是启动时传入的，重跑时 ach_daily_batch 会按
    # level=='major' 重新筛选——被移出的两个会自然从 majors 消失。
    m = json.loads(MATRIX_FILE.read_text(encoding="utf-8"))
    removed = [h["id"] for h in (m.get("hypotheses") or []) if h["id"] in TARGETS]
    if removed:
        m["hypotheses"] = [h for h in m["hypotheses"] if h["id"] not in TARGETS]
        # 保留 evidence 行（历史留痕），但 scoring 里移除
        sc = m.get("scoring") or {}
        for hid in removed:
            sc.pop(hid, None)
        m["scoring"] = sc
        MATRIX_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[RECLASS] 矩阵已移除 {removed}（evidence 行保留作历史留痕）")
    print("[RECLASS] 完成——下次 ach_daily_batch 将只诊断 6 个 major")


if __name__ == "__main__":
    main()
