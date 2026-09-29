# -*- coding: utf-8 -*-
r"""backfill_legacy_ach.py - 存量证据全量回填（2026-09-28 一次性工具）

**背景**：`local/ach_matrix.py` 的 `LEGACY_DEFAULT_ELIGIBLE = False` 让
4086 条"存量无标记"证据（9-12 建立准入制度之前挂载的）永久留在队列外。
当时的理由是诊断速度跟不上——mimo 45s/条，跑完要 51 小时。

**现在情况变了**：JEV 1.31s/条（实测），全量 89 分钟、$0.38。
抽样 200 条实测 C/I 率 1.88%（现有 993 行基准 2.4%），质量达标。

**为什么不直接改 LEGACY_DEFAULT_ELIGIBLE=True**：那会让 refresh 的每日
假设链每次都把 4000 条重新拉进队列，与"每日增量"设计冲突。本工具用
独立的准入判定（绕过 `_ach_eligible`），只做这一次回填。

**断点续跑**：每批落盘，进度记在 `data/.backfill_legacy_state.json`。
中断后重跑自动从上次位置继续。

用法：
    python tools/backfill_legacy_ach.py --dry              # 只看队列规模
    python tools/backfill_legacy_ach.py --batch 500        # 跑一批（500 条）
    python tools/backfill_legacy_ach.py --batch 500 --max-cost 0.4   # 带成本上限
    python tools/backfill_legacy_ach.py --reset            # 清进度重跑
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
STATE_FILE = BASE / ".backfill_legacy_state.json"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))

# JEV 计价（输入 $0.042/Mtok，输出免费）
JEV_RATE = 0.042 / 1_000_000


def build_legacy_queue(ach, majors):
    """存量未诊断证据（绕过 _ach_eligible 的准入限制）。

    只做最低限度的质量闸门：
      - summary 非空且 >= 15 字符（太短的标题无判断价值）
      - relevance >= 0.4（保留原设计的分档意图，但不再是硬拒绝）
    """
    diagnosed = {ev["key"] for ev in ach.data["evidence"]}
    from ach_matrix import evidence_key

    out, seen = [], set()
    for h in majors:
        for ev in h.get("evidence_log", []) or []:
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            if len(summary) < 15:
                continue
            key = evidence_key(ev)
            if key in diagnosed or key in seen:
                continue
            try:
                rel = float(ev.get("relevance") or 0)
            except (TypeError, ValueError):
                rel = 0.0
            if rel < 0.4:
                continue
            seen.add(key)
            out.append({"key": key, "hyp_id": h["id"], "ev": ev})
    # 按日期升序（历史顺序，便于观察后验随时间演化）
    out.sort(key=lambda e: str(e["ev"].get("date", "")))
    return out


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done_keys": [], "spent_usd": 0.0, "batches": 0}


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=500, help="本批诊断条数")
    ap.add_argument("--max-cost", type=float, default=0.40,
                    help="累计成本上限（美元），达到即停")
    ap.add_argument("--dry", action="store_true", help="只看队列，不调 AI")
    ap.add_argument("--reset", action="store_true", help="清空进度")
    ap.add_argument("--save-every", type=int, default=100, help="每 N 条落盘一次")
    args = ap.parse_args()

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("[BACKFILL] 进度已重置")

    import yaml
    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    from ach_matrix import ACHMatrix

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    if not majors:
        print("[BACKFILL] 无 major 假设 — 退出")
        return

    ach = ACHMatrix(BASE / "hypotheses" / "ach_matrix.json", majors)
    queue = build_legacy_queue(ach, majors)

    st = load_state()
    done = set(st.get("done_keys") or [])
    todo = [e for e in queue if e["key"] not in done]

    print(f"[BACKFILL] 存量队列 {len(queue)} 条 | 已完成 {len(done)} | 待跑 {len(todo)}")
    print(f"[BACKFILL] 累计花费 ${st.get('spent_usd', 0):.4f} | 上限 ${args.max_cost}")
    if args.dry or not todo:
        if todo:
            est = len(todo) * 1.31 / 60
            print(f"[BACKFILL] 预计 {est:.0f} 分钟 / ${len(todo) * 2240 * JEV_RATE:.3f}")
        return

    if st.get("spent_usd", 0) >= args.max_cost:
        print(f"[BACKFILL] 已达成本上限 ${args.max_cost}，停止")
        return

    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    analyzer = MacroAnalyzer(config, "", kb)

    from jev_client import JevClient
    jev = JevClient(caller="backfill_legacy")
    if not jev.available:
        print("[BACKFILL] JEV 不可用 — 退出（回填依赖 JEV 吞吐）")
        return
    print("[BACKFILL] 决策层: JEV")

    take = todo[:args.batch]
    t0 = time.time()
    done_n = failed = 0
    spent_before = st.get("spent_usd", 0.0)

    for i, e in enumerate(take, 1):
        # 成本闸门（按实测 2240 tok/条估算，实际以账本为准）
        est_spent = spent_before + done_n * 2240 * JEV_RATE
        if est_spent >= args.max_cost:
            print(f"[BACKFILL] 成本上限触及（估 ${est_spent:.3f}），本批停止")
            break
        try:
            diag = ach.ai_diagnose(e, analyzer, jev=jev)
            ach.record(e, diag, model="jev")
            done_n += 1
            done.add(e["key"])
        except Exception as ex:
            failed += 1
            print(f"[BACKFILL] {i} 失败: {str(ex)[:80]}")
        if i % args.save_every == 0:
            el = time.time() - t0
            print(f"[BACKFILL] {i}/{len(take)} | {el:.0f}s | {el / i:.2f}s/条 "
                  f"| 成功 {done_n} 失败 {failed}")
            st["done_keys"] = sorted(done)
            st["spent_usd"] = spent_before + done_n * 2240 * JEV_RATE
            st["batches"] = st.get("batches", 0)
            save_state(st)

    if done_n:
        print("[BACKFILL] 重算后验...")
        ach.bayesian_update(hyps)
        try:
            ach.sensitivity_analysis()
        except Exception as ex:
            print(f"[BACKFILL] sensitivity failed: {str(ex)[:100]}")
        ach.save()
        try:
            ach.export_markdown(BASE)
        except Exception as ex:
            print(f"[BACKFILL] export failed: {str(ex)[:100]}")
        hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    st["done_keys"] = sorted(done)
    st["spent_usd"] = spent_before + done_n * 2240 * JEV_RATE
    st["batches"] = st.get("batches", 0) + 1
    save_state(st)

    el = time.time() - t0
    print(f"\n[BACKFILL] 本批完成: 成功 {done_n} 失败 {failed} | {el / 60:.1f} 分钟 "
          f"| 估花费 ${st['spent_usd']:.4f}")
    print(f"[BACKFILL] 累计完成 {len(done)}/{len(queue)} | 矩阵 {len(ach.data['evidence'])} 行")
    remain = len(queue) - len(done)
    if remain:
        print(f"[BACKFILL] 剩 {remain} 条，再跑一次本命令继续（断点续跑）")


if __name__ == "__main__":
    main()
