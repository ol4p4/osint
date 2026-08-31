# -*- coding: utf-8 -*-
r"""fill_falsification.py - PLAN-2 M1 前置：批量补全假设树的 falsification_criteria
对每个空节点：AI 基于 title/rationale/indicators 生成 2~3 条可证伪判据
（可观察、有数字阈值、有时间线），写回 falsification_criteria + 补齐 indicators[].threshold_refute。
跑一次即弃（tools/ 目录）。用法：python tools/fill_falsification.py [--batch 4]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "local"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cloud"))

import yaml
from citizen_impact import call_ai, parse_json_array

HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")
BATCH = 4
DEADLINE_SECONDS = 600

PROMPT_TMPL = """以下是 {n} 条待证伪化的假设（政治经济学研判）。对每条生成 2~3 条"可证伪判据"：
要求：可观察（有具体数据源或事件）、有数字阈值或明确里程碑、有时间线。
同时检查该假设的 indicators（若有），把最能证伪它的那条阈值写进 refute 字段（原 refut 为空时）。

假设列表(JSON)：
{hyps_json}

严格只输出 JSON 数组（不要 markdown）：
[{{"id":"原id","falsification":"判据1；判据2；判据3"}}]"""


def main():
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    todo = [h for h in hyps if not h.get("falsification_criteria")]
    print(f"[FALSIFY] 待补全: {len(todo)}/{len(hyps)}")
    deadline = time.time() + DEADLINE_SECONDS
    filled = 0
    for i in range(0, len(todo), BATCH):
        if time.time() > deadline:
            print(f"[FALSIFY] 预算用尽, 剩余 {len(todo)-i} 条下轮继续")
            break
        batch = todo[i:i + BATCH]
        slim = [{"id": h["id"], "level": h.get("level"), "title": h.get("title"),
                 "rationale": (h.get("rationale") or "")[:200],
                 "indicators": h.get("indicators", [])} for h in batch]
        try:
            results = parse_json_array(call_ai(
                {"api": yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8")).get("api", {})},
                PROMPT_TMPL.format(n=len(batch), hyps_json=json.dumps(slim, ensure_ascii=False))))
        except Exception as e:
            print(f"[FALSIFY] batch {i} failed: {e}")
            continue
        by_id = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}
        for h in batch:
            r = by_id.get(h["id"])
            if r and r.get("falsification"):
                h["falsification_criteria"] = str(r["falsification"])[:500]
                # 顺带回填 indicators 里为空的 threshold_refute
                for ind in h.get("indicators", []) or []:
                    if isinstance(ind, dict) and not ind.get("threshold_refute"):
                        ind["threshold_refute"] = str(r["falsification"])[:200]
                        break
                filled += 1

    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[FALSIFY] 补全 {filled}/{len(todo)} 条 -> {HYP_FILE}")


if __name__ == "__main__":
    main()
