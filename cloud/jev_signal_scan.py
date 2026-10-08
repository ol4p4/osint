# -*- coding: utf-8 -*-
r"""jev_signal_scan.py - CI 侧 JEV 信号扫描（2026-10-08）

## 为什么需要它

此前 JEV 判断链**只在本地下跑得到**：CI 的 GitHub Secrets 只有 GEMINI/NVIDIA/OPENCODE，
没有 TYPESAFE_API_KEY。2026-10-08 接入 opencode.ai 的 **keyless 免费通道**
（模型 jev-1.13-free，匿名直连）后，CI 才第一次具备判定能力。

## 它做什么

对当日情报里最重要的 N 条，用生产同款 `gate_and_diagnose`（Noul 门控 → Choice 方向）
逐条判断「是否实质改变某个 major 假设的发生可能性」，产出当天的信号清单：

    jev_signals_YYYYMMDD.json   机器可读（含 gate/selectivity/code/conf）
    jev_signals_YYYYMMDD.md     人读摘要

## 为什么是"只读扫描"而不是"直接写 ACH 矩阵"

`data/hypotheses/` 的唯一有效写入方是本地（AGENTS.md 明确：CI 不碰假设树）。
历史教训：CI 上跑 link_intel_hyp / verify_hypotheses 会改 `data/hypotheses/`，
但 workflow 的 `git add` 列表不含该目录 → 每轮成果被 checkout 丢弃，白烧时长。
所以本步骤**只读假设树、只写自己的独立产物**，CI commit 时一并带走。

语义上也该分开：本地 ACH 是**累积式**（每日 cap、贝叶斯更新、去重挂载），
本扫描是**当日快照式**观察（不做累积、不改后验），用于线上实时看信号。

## 边界（实测）

- 免费端点 ~0.4~0.9s/次调用，每条证据 1~2 次调用 → 约 1~2s/条
- 端点不支持 Score 原语（返 error body），只支持 noul / choice —— 本脚本只用这两种
- 必须显式设 UA（urllib 默认 Python-urllib/3.x 被 Cloudflare 403）；已在 jev_client 内处理
- 端点不可达时**静默跳过**，绝不失败整个 CI job（判断是增益，不是关键路径）

用法：
    python cloud/jev_signal_scan.py --dir . --limit 100
    python cloud/jev_signal_scan.py --dry      # 只看候选，不调 AI
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "local"))

# 扫描上限：免费通道 ~1~2s/条，100 条约 3 分钟，CI job 现有 40min 预算余量充足
DEFAULT_LIMIT = 100
# 单次运行的时间预算（秒）：超时即停，已扫的部分照常落盘
DEFAULT_BUDGET = 420
# ACH 增量诊断上限：每轮诊断多少条待诊断证据（0=关闭）
DEFAULT_ACH_LIMIT = 60


def load_majors(hyp_file):
    """读 major 假设（只读，绝不写回）"""
    try:
        data = json.loads(Path(hyp_file).read_text(encoding="utf-8"))
    except Exception as ex:
        print("[JEV-SCAN] 读取假设树失败: " + str(ex)[:120])
        return []
    return [n for n in data if isinstance(n, dict) and n.get("level") == "major"]


def load_candidates(directory, limit):
    """当日情报里挑最重要的 limit 条（复用生产排序 + 跨源去重）"""
    files = sorted(glob.glob(os.path.join(str(directory), "intel_2*.jsonl")), reverse=True)
    files = [f for f in files if "raw" not in Path(f).name and "final" not in Path(f).name]
    if not files:
        return []
    # 只看最近 2 个文件（当日 + 昨日，覆盖跨零点）
    items = []
    seen = set()
    for f in files[:2]:
        try:
            for line in Path(f).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    it = json.loads(line)
                except Exception:
                    continue
                iid = it.get("id")
                if iid and iid not in seen:
                    seen.add(iid)
                    items.append(it)
        except Exception:
            continue
    if not items:
        return []
    try:
        from local.intel_gate import select_priority_unique
        return select_priority_unique(items, limit)
    except Exception:
        def _score(x):
            return float(x.get("final_score") or x.get("base_score") or 0)
        return sorted(items, key=lambda x: (_score(x), str(x.get("published_at") or "")),
                      reverse=True)[:limit]


def _text(item, cap=700):
    """**intel 条目**的取文：标题 + 正文片段（信号扫描路径用）。

    ⚠️ 不要用它处理假设树证据——证据的正文在 `summary`/`body`，此处不读，
    会得到空串（踩坑：ACH 路径首版复用了本函数，把空文本送进判定，
    结果 6 个 major 全判 N，台海实弹演习的 gate 掉到 0.11）。
    证据取文用 _evidence_text()。
    """
    title = str(item.get("cn_title") or item.get("title") or "")
    body = str(item.get("cn_summary") or item.get("content_preview") or "")
    text = title + ("\n" + body if body and body not in title else "")
    return text[:cap]


def _evidence_text(ev, cap=700):
    """**假设树证据**的取文（ACH 路径用）。

    字段口径对齐 `ach_matrix._diagnose_jev`：证据正文在 `summary` +
    `body`（2026-09-28 起证据带正文，此前只有 title[:100] 导致门控区分不出
    「A股军工板块拉升」与「解放军台海演习」）。
    """
    summary = str(ev.get("summary") or "")
    body = str(ev.get("body") or "")
    text = summary + ("\n" + body if body and body not in summary else "")
    return text[:cap]


def run_ach_diagnosis(majors, jev, out_dir, budget, limit):
    """ACH 增量诊断（2026-10-08 新增，让"判断"真正上云）。

    **为什么之前云端没有判断能力**：CI 的 Secrets 没有 TYPESAFE_API_KEY，
    JEV 判断链只在本地下跑得到；免 key 免费通道补上了这个缺口。

    **为什么必须只读假设树**：`data/hypotheses/` 的唯一有效写入方是本地
    （历史上 CI 跑 link/verify 因 git add 不含该目录，成果被 checkout 丢弃）。
    所以本函数**读**假设树的 evidence_log 找出未诊断证据、**读**矩阵避免重复，
    判定结果只写独立产物 `ach_diagnosis_YYYYMMDD.json`，由本地按需合并。

    判据与本地 `ach_matrix.find_undiagnosed` 一致：只收 `_ach_eligible` 的强证据
    （TF-IDF 命中，或 DOMAIN ≥0.4），避免把长尾弱证据灌进来。
    """
    hyp_file = ROOT / "data" / "hypotheses" / "active_hypotheses.json"
    matrix_file = ROOT / "data" / "hypotheses" / "ach_matrix.json"
    try:
        hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    except Exception as ex:
        print("[JEV-ACH] 读取假设树失败，跳过: " + str(ex)[:100])
        return None
    diagnosed = set()
    if matrix_file.exists():
        try:
            m = json.loads(matrix_file.read_text(encoding="utf-8"))
            diagnosed = {e.get("key") for e in (m.get("evidence") or [])}
        except Exception:
            pass
    print("[JEV-ACH] 矩阵已有诊断 %d 条" % len(diagnosed))

    # 收集未诊断证据（跨 major 去重，与本地 find_undiagnosed 同口径）
    try:
        sys.path.insert(0, str(ROOT / "local"))
        from ach_matrix import evidence_key, _ach_eligible
    except Exception as ex:
        print("[JEV-ACH] 无法导入 ach_matrix 判据，跳过: " + str(ex)[:100])
        return None

    pending = []
    seen = set()
    for h in hyps:
        if h.get("level") != "major":
            continue
        for ev in (h.get("evidence_log") or []):
            if not isinstance(ev, dict) or not ev.get("summary"):
                continue
            k = evidence_key(ev)
            if k in diagnosed or k in seen:
                continue
            if not _ach_eligible(ev):
                continue
            seen.add(k)
            pending.append({"key": k, "hyp_id": h.get("id"), "ev": ev})
    print("[JEV-ACH] 未诊断的准入证据 %d 条（上限 %d）" % (len(pending), limit))
    if not pending:
        return {"diagnosed": 0, "results": []}

    # 新证据优先（与本地一致：本地按 date 降序处理新证据）
    pending.sort(key=lambda e: str(e["ev"].get("date") or ""), reverse=True)
    pending = pending[:limit]

    t0 = time.time()
    results = []
    failed = 0
    for e in pending:
        if time.time() - t0 > budget:
            print("[JEV-ACH] 预算用尽")
            break
        ev = e["ev"]
        state = "证据（" + str(ev.get("date", "")) + "）：" + _evidence_text(ev)
        try:
            got = jev.gate_and_diagnose(state, majors)
        except Exception as ex:
            failed += 1
            if failed <= 3:
                print("[JEV-ACH] 诊断失败: " + str(ex)[:100])
            continue
        results.append({
            "key": e["key"],
            "hyp_id": e["hyp_id"],
            "date": ev.get("date"),
            "summary": str(ev.get("summary") or "")[:120],
            "diagnosis": {hid: {"code": v["code"], "conf": v["conf"],
                                "gate": v.get("gate"), "selectivity": v.get("selectivity")}
                          for hid, v in got.items()},
        })
    print("[JEV-ACH] 诊断 %d 条（失败 %d），耗时 %.0fs"
          % (len(results), failed, time.time() - t0))
    return {"diagnosed": len(results), "failed": failed, "results": results}


def main():
    ap = argparse.ArgumentParser(description="CI 侧 JEV 信号扫描 + ACH 增量诊断")
    ap.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录（CI 为仓库根）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="秒；超时即停")
    ap.add_argument("--out-dir", default=None, help="产物目录（默认与 --dir 相同）")
    ap.add_argument("--dry", action="store_true", help="只看候选不调 AI")
    ap.add_argument("--ach-limit", type=int, default=DEFAULT_ACH_LIMIT,
                    help="ACH 增量诊断上限（0=关闭，只做信号扫描）")
    args = ap.parse_args()

    out_dir = Path(args.out_dir or args.dir)
    hyp_file = ROOT / "data" / "hypotheses" / "active_hypotheses.json"

    majors = load_majors(hyp_file)
    if not majors:
        print("[JEV-SCAN] 无 major 假设，跳过")
        return
    print("[JEV-SCAN] major 假设 %d 个: %s"
          % (len(majors), ", ".join(m.get("id", "?") for m in majors)))

    cands = load_candidates(args.dir, args.limit)
    print("[JEV-SCAN] 候选 %d 条（按优先级+去重取样）" % len(cands))
    if args.dry or not cands:
        return

    try:
        from jev_client import JevClient
        jev = JevClient(caller="ci_signal_scan")
    except Exception as ex:
        print("[JEV-SCAN] 客户端初始化失败，跳过: " + str(ex)[:120])
        return
    if not jev.available:
        print("[JEV-SCAN] JEV 无可用通道，跳过")
        return
    print("[JEV-SCAN] 决策层: " + jev.describe())

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    signals = []
    scanned = failed = 0
    t0 = time.time()
    for it in cands:
        if time.time() - t0 > args.budget:
            print("[JEV-SCAN] 时间预算用尽，已扫 %d 条" % scanned)
            break
        text = _text(it)
        if not text:
            continue
        try:
            got = jev.gate_and_diagnose(text, majors)
        except Exception as ex:
            failed += 1
            if failed <= 3:
                print("[JEV-SCAN] 判定失败: " + str(ex)[:100])
            continue
        scanned += 1
        hits = []
        for hid, v in got.items():
            # 只记真正被推动的假设——与 ACH 准入同口径（code != N 即门控已通过）
            if v.get("code") in ("C", "I"):
                hits.append({"hyp_id": hid, "code": v["code"], "conf": v["conf"],
                             "gate": v.get("gate"), "selectivity": v.get("selectivity")})
        if hits:
            hits.sort(key=lambda x: -(x.get("gate") or 0))
            signals.append({
                "id": it.get("id"),
                "title": str(it.get("cn_title") or it.get("title") or "")[:120],
                "source": it.get("source_name") or it.get("source") or "",
                "published_at": str(it.get("published_at") or ""),
                "score": it.get("final_score") or it.get("base_score") or 0,
                "hits": hits,
            })

    payload = {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": jev.describe(),
        "majors": [{"id": m.get("id"), "title": m.get("title", "")[:80]} for m in majors],
        "scanned": scanned,
        "failed": failed,
        "signal_count": len(signals),
        "signals": signals,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / ("jev_signals_" + today + ".json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # ACH 增量诊断（2026-10-08）：让"判断"也上云，只写独立产物、不碰假设树
    if args.ach_limit and args.ach_limit > 0:
        ach = run_ach_diagnosis(majors, jev, out_dir, args.budget, args.ach_limit)
        if ach and ach.get("results"):
            ach_path = out_dir / ("ach_diagnosis_" + today + ".json")
            ach_path.write_text(json.dumps({
                "date": today,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "channel": jev.describe(),
                "note": "CI 侧只读诊断产物；本地按需合并进 ach_matrix.json",
                "diagnosed": ach["diagnosed"],
                "failed": ach.get("failed", 0),
                "results": ach["results"],
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            print("[JEV-ACH] 产物: " + ach_path.name)

    # 人读摘要
    title_of = {m.get("id"): m.get("title", "") for m in majors}
    lines = ["# JEV 信号扫描 - " + today, "",
             "扫描 %d 条 / 失败 %d 条 / 命中信号 %d 条（通道 %s）"
             % (scanned, failed, len(signals), jev.describe()), ""]
    if not signals:
        lines.append("当日无情报实质推动任何 major 假设。")
    else:
        by_hyp = {}
        for s in signals:
            for h in s["hits"]:
                by_hyp.setdefault(h["hyp_id"], []).append((s, h))
        for hid, rows in sorted(by_hyp.items(), key=lambda kv: -len(kv[1])):
            lines.append("## %s %s（%d 条）" % (hid, title_of.get(hid, ""), len(rows)))
            lines.append("")
            for s, h in sorted(rows, key=lambda r: -(r[1].get("gate") or 0))[:8]:
                lines.append("- `%s` gate=%.2f → %s(%.2f) ｜ %s"
                             % (h["code"], h.get("gate") or 0, hid, h["conf"], s["title"]))
            lines.append("")
    md_path = out_dir / ("jev_signals_" + today + ".md")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    el = time.time() - t0
    print("[JEV-SCAN] 完成: 扫描 %d / 失败 %d / 信号 %d 条，耗时 %.0fs（%.2fs/条）"
          % (scanned, failed, len(signals), el, el / max(scanned, 1)))
    print("[JEV-SCAN] 产物: " + json_path.name + " / " + md_path.name)


if __name__ == "__main__":
    main()
