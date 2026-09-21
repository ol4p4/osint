# -*- coding: utf-8 -*-
r"""rerun_ach.py - ACH 判定重跑（用 JEV 重判历史证据，2026-09-21）

**为什么需要**：Phase 0 实测发现历史 C/I 标签是噪声——mimo 把「韩国加息」
「日经指数涨跌」「黄金欧元行情」判给「AI成本上升」假设（过度联想），
把「日经低开」「美元兑日元」判给「台海冲突升级」（市场行情误当军事信号）。
模拟显示仅替换 60 行，台海后验就从 0.934 掉到 0.426。

**时机**：resolutions.jsonl 尚不存在（首个到期 2026-12-05），
此刻重跑改的是"未被验证的猜测"，不是"已验证的结论"——再晚就会污染校准数据。

**安全设计**：
  - 只重跑**已有判定**的行（不碰 find_undiagnosed 队列，那是日常批的事）
  - 判定记忆层（model/diagnosed_at/diagnosis_log.jsonl）保证可对比可回滚
  - 每 N 条自动落盘，中断不丢进度
  - 默认先跑小批（--limit），全量需显式 --all

用法：
    python tools/rerun_ach.py --limit 50          # 小批试跑（推荐先做）
    python tools/rerun_ach.py --limit 50 --dry    # 只看会重跑哪些
    python tools/rerun_ach.py --all               # 全量重跑
    python tools/rerun_ach.py --only-ci           # 只重跑含 C/I 的行（影响后验的）
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))


def posterior(rows, hyp):
    """与 ACHMatrix.bayesian_update 同源的后验计算（供 before/after 对比）"""
    prior = float(hyp.get("base_confidence") or 0.5)
    odds = prior / max(1 - prior, 0.01)
    for e in rows:
        d = (e.get("diagnosis") or {}).get(hyp["id"])
        if d:
            odds *= d.get("lr", 1.0)
    p = odds / (1 + odds)
    return max(min(p, 0.95), 0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="重跑条数（0=需显式 --all）")
    ap.add_argument("--all", action="store_true", help="全量重跑")
    ap.add_argument("--only-ci", action="store_true", help="只重跑含 C/I 的行")
    ap.add_argument("--dry", action="store_true", help="只列清单不调 API")
    ap.add_argument("--save-every", type=int, default=50, help="每 N 条落盘一次")
    args = ap.parse_args()

    from ach_matrix import ACHMatrix, derive_lr
    from jev_client import JevClient

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    matrix_file = BASE / "hypotheses" / "ach_matrix.json"
    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    if not majors:
        print("[RERUN] no major hypotheses — abort")
        return

    ach = ACHMatrix(matrix_file, majors)

    # 候选 = 已有判定的行（重跑对象）
    rows = ach.data["evidence"]
    cand = []
    for e in rows:
        ds = (e.get("diagnosis") or {}).values()
        if not ds:
            continue
        if args.only_ci and not any(d.get("code") in ("C", "I") for d in ds):
            continue
        cand.append(e)

    if not args.all and not args.limit:
        print(f"[RERUN] 候选 {len(cand)} 行。需显式指定 --limit N（小批）或 --all（全量）")
        print("[RERUN] 建议先小批：python tools/rerun_ach.py --limit 50")
        return
    take = cand if args.all else cand[:args.limit]
    print(f"[RERUN] 候选 {len(cand)} 行，本次重跑 {len(take)} 行"
          f"{'（仅含 C/I）' if args.only_ci else ''}")

    if args.dry:
        for e in take[:15]:
            ci = [(d.get("code"), d.get("lr")) for d in (e.get("diagnosis") or {}).values()
                  if d.get("code") in ("C", "I")]
            print(f"   - {e.get('summary', '')[:52]}")
            print(f"       现有 C/I: {ci if ci else '无'} | model={e.get('model')}")
        print(f"[RERUN] --dry 结束（共 {len(take)} 行待重跑）")
        return

    jev = JevClient(caller="rerun_ach")
    if not jev.available:
        print("[RERUN] JEV key 未配置，中止（重跑的目的就是用 JEV 覆盖旧判定）")
        return

    # 重跑前的后验（对比基准）
    before = {h["id"]: posterior(rows, h) for h in majors}

    t0 = time.time()
    done = failed = 0
    for i, e in enumerate(take, 1):
        # 矩阵行是 {key,date,summary,diagnosis,model,...}，而 ai_diagnose 期望
        # {key, ev:{date,summary,source}} —— 做一次结构适配（source 矩阵行不存）
        entry = {"key": e.get("key"),
                 "ev": {"date": e.get("date", ""), "summary": e.get("summary", ""), "source": ""}}
        try:
            diag = ach.ai_diagnose(entry, None, jev=jev)
            ach.record(entry, diag, model="jev")
            done += 1
        except Exception as ex:
            failed += 1
            print(f"  [{i}] FAIL {type(ex).__name__}: {str(ex)[:80]}")
        if done and done % args.save_every == 0:
            ach.save()
            print(f"  [{i}/{len(take)}] 已落盘（成功 {done} 失败 {failed}）")
        if i % 25 == 0:
            el = time.time() - t0
            print(f"  [{i}/{len(take)}] {el:.0f}s 已用，约 {el / i * (len(take) - i):.0f}s 剩余")

    ach.save()
    el = time.time() - t0
    print()
    print("=" * 60)
    print(f"[RERUN] 完成 {done}/{len(take)}（失败 {failed}），耗时 {el:.1f}s（{el / max(done,1):.2f}s/条）")

    # 后验对比
    scores = ach.bayesian_update(hyps)
    print()
    print("--- 后验变化（重跑前 → 重跑后）---")
    titles = {h["id"]: h.get("title", "")[:26] for h in majors}
    order = sorted(majors, key=lambda h: -before[h["id"]])
    for h in order:
        hid = h["id"]
        b, a = before[hid], scores[hid]["posterior"]
        flag = " <<<" if abs(a - b) > 0.05 else ""
        print(f"  {titles[hid]:28s} {b:.3f} -> {a:.3f}  ({a - b:+.3f}){flag}")
    rb = [titles[h["id"]] for h in sorted(majors, key=lambda x: -before[x["id"]])]
    ra = [titles[h["id"]] for h in sorted(majors, key=lambda x: -scores[x["id"]]["posterior"])]
    print()
    print(f"  排名变化: {'是' if rb != ra else '否'}")
    if rb != ra:
        print(f"    改前 Top3: {rb[:3]}")
        print(f"    改后 Top3: {ra[:3]}")

    ach.sensitivity_analysis()
    ach.save()
    hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        ach.export_markdown(BASE)
    except Exception as ex:
        print(f"[RERUN] export failed: {str(ex)[:80]}")
    print()
    print(f"[RERUN] 矩阵已更新（{len(ach.data['evidence'])} 行），假设树 confidence 已同步")
    print(f"[RERUN] 回滚点: data/hypotheses/*.bak_* + git history")


if __name__ == "__main__":
    main()
