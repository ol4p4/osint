# -*- coding: utf-8 -*-
r"""ach_daily_batch.py - ACH 每日增量诊断批（2026-09-10 新增）
背景：周循环每轮只能诊断 20 条证据（MAX_DIAGNOSE_PER_RUN），而积压 885 条 —
按周跑要 45 周。本入口每日消化一批（默认 60 条、900s 预算），新证据优先，
约 2~3 周清完成史积压，之后跟得上每日新增。

由 refresh.py 的 run_hypothesis_chain() 调用（20h 节流）；也可手动：
    python tools/ach_daily_batch.py [--batch 60] [--budget 900] [--dry] [--force]
成功落盘后写 data/.ach_last_run（节流戳）；失败不写，下轮重试。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
STATE_FILE = BASE / ".ach_last_run"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
sys.path.insert(0, str(PROJECT / "cloud"))

BATCH = 60
BUDGET_SECONDS = 900


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--budget", type=int, default=BUDGET_SECONDS)
    ap.add_argument("--dry", action="store_true", help="只统计待诊断数，不调 AI")
    ap.add_argument("--force", action="store_true", help="忽略 20h 节流")
    ap.add_argument("--throttle-hours", type=float, default=20.0)
    args = ap.parse_args()

    if not args.force and not args.dry and STATE_FILE.exists():
        try:
            age_h = (time.time() - float(STATE_FILE.read_text(encoding="utf-8").strip())) / 3600
            if age_h < args.throttle_hours:
                print(f"[ACH-BATCH] skip (上次成功 {age_h:.1f}h 前 < {args.throttle_hours}h)")
                return
        except Exception:
            pass

    import yaml
    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    from ach_matrix import ACHMatrix

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    if not hyp_file.exists():
        print(f"[ACH-BATCH] hypotheses not found: {hyp_file} — skip")
        return
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    if not majors:
        print("[ACH-BATCH] no major hypotheses — skip")
        return

    ach = ACHMatrix(BASE / "hypotheses" / "ach_matrix.json", majors)
    undiag = ach.find_undiagnosed(limit=args.batch, newest_first=True)
    print(f"[ACH-BATCH] 待诊断 {len(undiag)} 条（本批上限 {args.batch}）")
    if args.dry or not undiag:
        return

    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    analyzer = MacroAnalyzer(config, "", kb)

    deadline_ts = time.time() + args.budget
    done = failed = 0
    for e in undiag:
        if time.time() > deadline_ts:
            print(f"[ACH-BATCH] 预算用尽，剩余 {len(undiag) - done - failed} 条下轮继续")
            break
        try:
            diag = ach.ai_diagnose(e, analyzer)
            ach.record(e, diag)
            done += 1
        except Exception as ex:
            failed += 1
            print(f"[ACH-BATCH] diagnose failed: {str(ex)[:100]}")
    if done:
        ach.bayesian_update(hyps)
        try:
            ach.sensitivity_analysis()
        except Exception as ex:
            print(f"[ACH-BATCH] sensitivity failed: {str(ex)[:100]}")
        ach.save()
        try:
            ach.export_markdown(BASE)
        except Exception as ex:
            print(f"[ACH-BATCH] export failed: {str(ex)[:100]}")
        # 矩阵更新的置信度同步回假设树（bayesian_update 只改内存对象）
        hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE_FILE.write_text(str(time.time()), encoding="utf-8")
    print(f"[ACH-BATCH] 完成: 诊断 {done} 条, 失败 {failed}, 矩阵共 "
          f"{len(ach.data['evidence'])} 行 -> {STATE_FILE}")


if __name__ == "__main__":
    main()
