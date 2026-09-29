# -*- coding: utf-8 -*-
r"""rerun_all_ach.py - 全量重判矩阵（2026-09-29）

**用途**：门控措辞 + 双判据 + 正文回填三项改进后，存量 4181 行的判定
全部基于旧口径（问"是否涉及议题"、无正文、无 selectivity）。本工具按
新口径重判全部证据。

**为什么不能只重判 C/I 行**：新措辞下 N 判定也可能变成 C/I——
"某情报是否改变概率"与"是否涉及议题"是不同问题，前者更严格但也更准，
原来被误判为 N 的真信号会被捞回来。

**断点续跑**：每批落盘，进度记 `data/.rerun_all_state.json`。

用法：
    python tools/rerun_all_ach.py --dry              # 只看规模
    python tools/rerun_all_ach.py --batch 600        # 跑一批
    python tools/rerun_all_ach.py --reset            # 清进度
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
STATE_FILE = BASE / ".rerun_all_state.json"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))

JEV_RATE = 0.042 / 1_000_000
TOK_PER_ITEM = 2240   # 实测均值（含两段式两次调用）


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done_keys": [], "batches": 0}


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=600)
    ap.add_argument("--max-cost", type=float, default=0.60)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--save-every", type=int, default=100)
    args = ap.parse_args()

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("[RERUN-ALL] 进度已重置")

    import yaml
    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    from ach_matrix import ACHMatrix

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    ach = ACHMatrix(BASE / "hypotheses" / "ach_matrix.json", majors)

    # 重判对象 = 矩阵里已有的全部证据行
    rows = ach.data["evidence"]
    st = load_state()
    done = set(st.get("done_keys") or [])
    todo = [r for r in rows if r["key"] not in done]

    print(f"[RERUN-ALL] 矩阵 {len(rows)} 行 | 已完成 {len(done)} | 待重判 {len(todo)}")
    print(f"[RERUN-ALL] 预计 {len(todo) * 1.3 / 60:.0f} 分钟 / ${len(todo) * TOK_PER_ITEM * JEV_RATE:.3f}")
    if args.dry or not todo:
        return

    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    analyzer = MacroAnalyzer(config, "", kb)

    from jev_client import JevClient
    jev = JevClient(caller="rerun_all_ach")
    if not jev.available:
        print("[RERUN-ALL] JEV 不可用 — 退出")
        return

    # 建证据 key → evidence_entry（含 body）索引
    ev_by_key = {}
    for h in hyps:
        for e in (h.get("evidence_log") or []):
            if not isinstance(e, dict) or not e.get("summary"):
                continue
            from ach_matrix import evidence_key
            ev_by_key[evidence_key(e)] = {"key": evidence_key(e), "hyp_id": h["id"], "ev": e}

    take = todo[:args.batch]
    t0 = time.time()
    done_n = failed = 0
    for i, row in enumerate(take, 1):
        entry = ev_by_key.get(row["key"])
        if not entry:
            done.add(row["key"])   # 原始证据已不在树里，跳过
            continue
        try:
            diag = ach.ai_diagnose(entry, analyzer, jev=jev)
            ach.record(entry, diag, model="jev")
            done_n += 1
            done.add(row["key"])
        except Exception as ex:
            failed += 1
            print(f"[RERUN-ALL] {i} 失败: {str(ex)[:70]}")
        if i % args.save_every == 0:
            el = time.time() - t0
            print(f"[RERUN-ALL] {i}/{len(take)} | {el:.0f}s | {el / i:.2f}s/条 "
                  f"| 成功 {done_n} 失败 {failed}")
            st["done_keys"] = sorted(done)
            save_state(st)

    if done_n:
        print("[RERUN-ALL] 重算后验...")
        ach.bayesian_update(hyps)
        try:
            ach.sensitivity_analysis()
        except Exception as ex:
            print(f"[RERUN-ALL] sensitivity failed: {str(ex)[:90]}")
        ach.save()
        try:
            ach.export_markdown(BASE)
        except Exception as ex:
            print(f"[RERUN-ALL] export failed: {str(ex)[:90]}")
        hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    st["done_keys"] = sorted(done)
    st["batches"] = st.get("batches", 0) + 1
    save_state(st)
    el = time.time() - t0
    print(f"\n[RERUN-ALL] 本批: 成功 {done_n} 失败 {failed} | {el / 60:.1f} 分钟")
    print(f"[RERUN-ALL] 累计 {len(done)}/{len(rows)} | 剩 {len(rows) - len(done)} 条")


if __name__ == "__main__":
    main()
