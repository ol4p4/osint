# -*- coding: utf-8 -*-
r"""calibration.py - 同类方案调研 P0-1：假设置信度校准评分（学 Anamnesis）
读 data/hypotheses/resolutions.jsonl（由 hypothesis_engine.record_resolution 验证时落盘），
计算 Brier score 并按 Murphy 三分解拆成：
  reliability（可靠性/校准度，越小越好：预测概率与实际命中率差）
  resolution（分辨度，越大越好：能不能把"会发生"和"不会发生"分开）
  uncertainty（题目本身难度，基线熵，系统无法改善）
Brier = reliability − resolution + uncertainty（二值结果下的恒等式）
关键价值：区分"校准好但没分辨力"（永远报基准概率）和"分辨力好但过度自信"。

用法：python local/calibration.py   → 写 data/calibration.json（gen_dashboard 校准面板读取）
纯标准库。
"""
import json
import math
import sys
import time
from pathlib import Path

BASE = Path(r"D:\osint\data")
RESOLUTIONS_FILE = BASE / "hypotheses" / "resolutions.jsonl"
OUT_FILE = BASE / "calibration.json"
N_BINS = 10
MIN_SAMPLES = 5          # 少于此样本数时标记 sufficient=False（面板显示"样本不足"）

# outcome → 客观结果 o（confirmed=发生了 / refuted=没发生 / partial=对一半）
OUTCOME_VALUE = {"confirmed": 1.0, "refuted": 0.0, "partial": 0.5}


def load_resolutions(path=None):
    """读 resolutions.jsonl，只保留可算校准的记录（有明确结果 + 有预测概率）"""
    f = Path(path) if path else RESOLUTIONS_FILE
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("outcome") not in OUTCOME_VALUE:
            continue
        try:
            p = float(e.get("confidence_at_deadline"))
        except (TypeError, ValueError):
            continue
        if not (0.0 <= p <= 1.0):
            continue
        out.append({"hyp_id": e.get("hyp_id"), "title": e.get("title", ""),
                    "outcome": e["outcome"], "p": p, "o": OUTCOME_VALUE[e["outcome"]],
                    "resolved_at": e.get("resolved_at", "")})
    return out


def compute_calibration(entries):
    """Brier + Murphy 三分解 + 十桶校准曲线"""
    n = len(entries)
    if n == 0:
        return {"n_resolved": 0, "sufficient": False, "note": "暂无已验证假设"}
    # 全局 Brier = mean((p-o)^2)
    brier = sum((e["p"] - e["o"]) ** 2 for e in entries) / n
    base_rate = sum(e["o"] for e in entries) / n
    uncertainty = base_rate * (1 - base_rate)

    # 分桶（按预测概率）
    bins = []
    reliability = 0.0
    resolution = 0.0
    for k in range(N_BINS):
        lo, hi = k / N_BINS, (k + 1) / N_BINS
        bucket = [e for e in entries if (lo <= e["p"] < hi) or (k == N_BINS - 1 and e["p"] == hi)]
        if bucket:
            p_bar = sum(e["p"] for e in bucket) / len(bucket)
            o_bar = sum(e["o"] for e in bucket) / len(bucket)
            reliability += (len(bucket) / n) * (p_bar - o_bar) ** 2
            resolution += (len(bucket) / n) * (o_bar - base_rate) ** 2
            bins.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": len(bucket),
                         "avg_prediction": round(p_bar, 3), "avg_outcome": round(o_bar, 3)})
        else:
            bins.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": 0,
                         "avg_prediction": None, "avg_outcome": None})

    # 浮点闭合检查（理论恒等式 Brier = rel − res + unc，仅当所有预测都落进分桶）
    return {
        "n_resolved": n,
        "sufficient": n >= MIN_SAMPLES,
        "base_rate": round(base_rate, 3),
        "brier": round(brier, 4),
        "reliability": round(reliability, 4),
        "resolution": round(resolution, 4),
        "uncertainty": round(uncertainty, 4),
        "decomposition_check": round(reliability - resolution + uncertainty, 4),
        "bins": bins,
        "entries": [{"hyp_id": e["hyp_id"], "title": e["title"][:60], "outcome": e["outcome"],
                     "p": round(e["p"], 3), "o": e["o"], "resolved_at": e["resolved_at"]}
                    for e in entries[-50:]],  # 最近 50 条明细（面板可追溯）
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def main():
    entries = load_resolutions()
    result = compute_calibration(entries)
    OUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result["n_resolved"] == 0:
        print("[CALIB] 无已验证假设，写空骨架 ->", OUT_FILE)
        return
    print(f"[CALIB] n={result['n_resolved']} brier={result['brier']} "
          f"(reliability={result['reliability']} resolution={result['resolution']} "
          f"uncertainty={result['uncertainty']}) -> {OUT_FILE}")
    # 自检：三分解之和应≈全局 Brier
    drift = abs(result["decomposition_check"] - result["brier"])
    if drift > 0.01:
        print(f"[CALIB] 警告: 三分解闭合偏差 {drift:.4f}（>0.01，检查分桶覆盖）")
    else:
        print(f"[CALIB] 三分解闭合校验通过 (偏差 {drift:.4f})")


if __name__ == "__main__":
    main()
