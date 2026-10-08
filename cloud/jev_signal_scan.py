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
    """证据文本：标题 + 正文片段（对齐 ach_matrix._diagnose_jev 的取文方式）"""
    title = str(item.get("cn_title") or item.get("title") or "")
    body = str(item.get("cn_summary") or item.get("content_preview") or "")
    text = title + ("\n" + body if body and body not in title else "")
    return text[:cap]


def main():
    ap = argparse.ArgumentParser(description="CI 侧 JEV 信号扫描")
    ap.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录（CI 为仓库根）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="秒；超时即停")
    ap.add_argument("--out-dir", default=None, help="产物目录（默认与 --dir 相同）")
    ap.add_argument("--dry", action="store_true", help="只看候选不调 AI")
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
