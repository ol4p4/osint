# -*- coding: utf-8 -*-
r"""merge_ci_diagnosis.py - 合并云端 ACH 诊断产物（2026-10-12 新增）

## 为什么需要它

`cloud/jev_signal_scan.py` 自 2026-10-08 起在 CI 上跑 ACH 增量诊断，产物
`ach_diagnosis_YYYYMMDD.json` 随 CI commit 带回本地——但**此前没有任何代码读它**：
refresh.py 的 15 个步骤里没有合并步骤，全仓库搜 `ach_diagnosis` 只有生成方自己。
结果是云端每天免费诊断的成果（免 key JEV 通道）拉回本地后躺在仓库里没人用，
本地又用自己的 JEV（挂掉时回退 mimo，45s/条）重跑一遍同样的证据。

## 它做什么

把 CI 产物里**本地矩阵尚未诊断**的证据补录进 `ach_matrix.json`，然后与本地
`ach_daily_batch` 完全同路径收尾：bayesian_update → sensitivity → save →
export_markdown → 置信度回写假设树。

**它不做任何 AI 调用**——纯本地合并，所以零成本、秒级完成，可每轮跑。

## 三道闸门（缺一不可）

1. **证据必须在当前假设树里**：产物 key 要能在 evidence_log 里找到。否则会往矩阵灌
   「孤儿行」——bayesian_update 遍历的是矩阵行，孤儿行照样影响后验，但它的证据
   已不在树里，无从核对。
2. **只补未诊断，绝不覆盖**：本地是假设树的唯一有效写入方（`data/hypotheses/`
   历史教训）。已有行（哪怕是全 N）一律跳过——否则同一证据会被 CI 与本地反复
   翻转，后验churn。
3. **准入判据与生产同源**：`_ach_eligible` + `evidence_key` 均 import 自
   `ach_matrix`，不复制常量（AGENTS.md：「清理脚本的阈值必须从生产代码导入」）。

## 用法

    python tools/merge_ci_diagnosis.py --dry      # 只看候选，不写盘
    python tools/merge_ci_diagnosis.py            # 真合并
    python tools/merge_ci_diagnosis.py --base <沙箱data目录>   # 改矩阵/树位置（测试用）

由 refresh.py 的 run_hypothesis_chain 调用，排在 ach-batch **之前**——CI 已诊断的
证据先入矩阵，本地 ach-batch 的有限预算（1600s）就能花在别处。
critical=False：产物来自外部（CI），缺失/损坏不该阻塞核心链节流戳（同 probe 教训）。
"""
import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\osint")
DEFAULT_BASE = PROJECT / "data"
# 时间线失衡警戒线（AGENTS.md「回填历史必须检查时间线失衡」）：新增行占全库比例
# 超过此值时告警——ACH 后验是累加更新，单期数据量翻倍会让后验被该期主导。
TIMELINE_WARN_RATIO = 0.25
# 后验单次位移告警线：一次合并让某个 major 的后验移动超过此值就打印醒目提示。
POSTERIOR_SHIFT_WARN = 0.15


def _product_date(path):
    """从 ach_diagnosis_YYYYMMDD.json 文件名取日期，失败返 None"""
    stem = Path(path).stem
    parts = stem.split("_")
    for p in reversed(parts):
        if len(p) == 8 and p.isdigit():
            return p
    return None


def load_products(directory, days):
    """读近 days 天的 CI 诊断产物，返回 ({key: record}, [用到的文件名])。

    同一 key 出现在多个产物时以**日期更新**的为准（后写覆盖先写）。
    """
    files = sorted(glob_products(directory), key=lambda p: _product_date(p) or "")
    if not files:
        return {}, []
    cutoff = None
    if days > 0:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    merged = {}
    used = []
    for f in files:
        d = _product_date(f)
        if cutoff and d and d < cutoff:
            continue
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as ex:
            print("[MERGE-CI] 读取失败 %s: %s" % (Path(f).name, str(ex)[:80]))
            continue
        results = data.get("results") or []
        if not results:
            continue
        used.append(Path(f).name)
        for r in results:
            if isinstance(r, dict) and r.get("key"):
                merged[r["key"]] = r
    return merged, used


def glob_products(directory):
    return [str(p) for p in Path(directory).glob("ach_diagnosis_*.json")]


def build_evidence_index(majors, evidence_key):
    """当前假设树里所有 major 证据的 key → ev 索引。

    只收 evidence_log 里有 summary 的条目——与 ACHMatrix.find_undiagnosed
    同口径（无 summary 的证据 record() 也写不出可核对的 summary）。
    """
    idx = {}
    for h in majors:
        for ev in (h.get("evidence_log") or []):
            if isinstance(ev, dict) and ev.get("summary"):
                idx.setdefault(evidence_key(ev), ev)
    return idx


def main():
    ap = argparse.ArgumentParser(description="合并 CI 侧 ACH 诊断产物进本地矩阵")
    ap.add_argument("--dir", default=str(PROJECT), help="CI 产物所在目录")
    ap.add_argument("--base", default=str(DEFAULT_BASE), help="data 目录（矩阵/树所在）")
    ap.add_argument("--days", type=int, default=7, help="只合并近 N 天产物（0=不限）")
    ap.add_argument("--dry", action="store_true", help="只看候选，不写盘")
    ap.add_argument("--sensitivity", action="store_true",
                    help="顺带重算敏感性分析（O(n^2)，大矩阵很慢，默认跳过）")
    args = ap.parse_args()

    base = Path(args.base)
    hyp_file = base / "hypotheses" / "active_hypotheses.json"
    matrix_file = base / "hypotheses" / "ach_matrix.json"
    if not hyp_file.exists():
        print("[MERGE-CI] 假设树不存在: " + str(hyp_file))
        return

    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(PROJECT / "local"))
    try:
        from ach_matrix import ACHMatrix, evidence_key, _ach_eligible
    except Exception as ex:
        print("[MERGE-CI] 无法导入 ach_matrix: " + str(ex)[:100])
        return

    hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    if not majors:
        print("[MERGE-CI] 无 major 假设，跳过")
        return

    products, used_files = load_products(args.dir, args.days)
    if not products:
        print("[MERGE-CI] 无可用 CI 诊断产物，跳过")
        return
    print("[MERGE-CI] 产物 %d 个: %s" % (len(used_files), ", ".join(used_files[:6])))

    ach = ACHMatrix(matrix_file, majors)
    diagnosed = {ev.get("key") for ev in ach.data.get("evidence", [])}
    print("[MERGE-CI] 矩阵现有 %d 行" % len(diagnosed))

    ev_index = build_evidence_index(majors, evidence_key)
    print("[MERGE-CI] 假设树证据 %d 条（可核对）" % len(ev_index))

    # 三道闸门：树里有 / 矩阵没有 / 准入
    pending = []
    skipped_no_ev = skipped_done = skipped_ineligible = 0
    for key, rec in products.items():
        if key not in ev_index:
            skipped_no_ev += 1
            continue
        if key in diagnosed:
            skipped_done += 1
            continue
        ev = ev_index[key]
        if not _ach_eligible(ev):
            skipped_ineligible += 1
            continue
        pending.append((key, rec, ev))

    print("[MERGE-CI] 候选 %d 条（跳过：矩阵已诊断 %d / 树中无此证据 %d / 非准入 %d）"
          % (len(pending), skipped_done, skipped_no_ev, skipped_ineligible))
    if not pending:
        print("[MERGE-CI] 无可补录，退出（幂等）")
        return

    # 有多少候选带 C/I（真正推动后验的）——给个预期
    ci_n = 0
    for _k, rec, _ev in pending:
        for _hid, d in (rec.get("diagnosis") or {}).items():
            if d.get("code") in ("C", "I"):
                ci_n += 1
                break
    print("[MERGE-CI] 其中带 C/I 判定的 %d 条" % ci_n)

    # 时间线失衡检查（回填前先算占比）
    total_after = len(diagnosed) + len(pending)
    ratio = len(pending) / max(total_after, 1)
    flag = " ⚠️ 超警戒线" if ratio > TIMELINE_WARN_RATIO else ""
    print("[MERGE-CI] 新增占比 %.1f%%（警戒 %.0f%%）%s"
          % (ratio * 100, TIMELINE_WARN_RATIO * 100, flag))

    if args.dry:
        for key, rec, _ev in pending[:5]:
            print("  [候选] %s %s | %s" % (key, rec.get("date", ""),
                                            str(rec.get("summary", ""))[:60]))
        print("[MERGE-CI] --dry 结束，未写盘")
        return

    # 合并前后验快照（用于报告位移；不写盘）
    before = {hid: s.get("posterior")
              for hid, s in (ach.data.get("scoring") or {}).items()}

    # 写前备份（矩阵与假设树都是多写入方共享的状态文件；假设树 33MB 且是
    # 唯一资产，只有 git 追踪不够，.bak 是廉价保险）
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for f in (matrix_file, hyp_file):
            if f.exists():
                bak = f.with_name(f.name + ".bak_ci_" + stamp)
                shutil.copy2(str(f), str(bak))
                print("[MERGE-CI] 备份: " + bak.name)
    except Exception as ex:
        print("[MERGE-CI] 备份失败（继续）: " + str(ex)[:80])

    done = 0
    for key, rec, ev in pending:
        diag = []
        for hid, d in (rec.get("diagnosis") or {}).items():
            if not isinstance(d, dict):
                continue
            entry = {"hyp_id": hid}
            entry.update(d)
            diag.append(entry)
        if not diag:
            continue
        # 走本地同一条 record() 路径：LR 由 derive_lr(code, conf, gate) 推导，
        # 判定记忆写入 diagnosis_log.jsonl
        ach.record({"key": key, "ev": ev}, diag, model="jev-ci")
        done += 1
    print("[MERGE-CI] 已补录 %d 条" % done)

    if done:
        ach.bayesian_update(hyps)
        if args.sensitivity:
            try:
                ach.sensitivity_analysis()
            except Exception as ex:
                print("[MERGE-CI] sensitivity failed: " + str(ex)[:100])
        ach.save()
        try:
            ach.export_markdown(base)
        except Exception as ex:
            print("[MERGE-CI] export failed: " + str(ex)[:100])
        # 置信度回写假设树（bayesian_update 只改内存对象）
        hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    # 后验位移报告
    after = {hid: s.get("posterior")
             for hid, s in (ach.data.get("scoring") or {}).items()}
    print("[MERGE-CI] 后验变化:")
    for hid in sorted(after):
        b, a = before.get(hid), after.get(hid)
        if b is None or a is None:
            continue
        delta = (a - b) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else 0
        mark = " ⚠️ 位移偏大" if abs(delta) >= POSTERIOR_SHIFT_WARN else ""
        # 行首带 [MERGE-CI] 标记，refresh 的链日志才会抓取（否则细节被吞）
        print("[MERGE-CI]     %s: %s → %s (%s)%s"
              % (hid, b, a, ("%+.3f" % delta), mark))
    print("[MERGE-CI] 完成: 补录 %d 条, 矩阵 %d 行"
          % (done, len(ach.data.get("evidence", []))))


if __name__ == "__main__":
    main()
