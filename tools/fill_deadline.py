# -*- coding: utf-8 -*-
r"""fill_deadline.py - 同类方案调研 P0-2：批量回填假设树的验证期限 deadline
背景：70/70 节点 deadline 不可用（63 个空 + 7 个 2028~2036 远期），而
hypothesis_engine.get_due_hypotheses() 只验证 deadline<=today 的节点 → 验证循环从不触发。
对每个问题节点：AI 基于 title/falsification_criteria/indicators 提议 1~24 个月的验证期限；
AI 失败按 level 兜底（small 3 / medium 6 / major 12 / mega 24 个月）。
跑一次即弃（tools/ 目录）。用法：python tools/fill_deadline.py [--batch 6]
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "local"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cloud"))

import yaml
from citizen_impact import call_ai, parse_json_array

HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")
BATCH = 6
DEADLINE_SECONDS = 600
MAX_MONTHS = 24          # 期限上限：超过 2 年的验证期限等于永不触发

LEVEL_DEFAULT_MONTHS = {"small": 3, "medium": 6, "major": 12, "mega": 24}

PROMPT_TMPL = """以下是 {n} 条政治经济学假设。对每条判断一个合理的"验证期限"（多少个月后回来核对它对不对）：
依据：假设的时间敏感度 + 证伪判据里提到的时间线 + indicators 的数据发布频率。
规则：月数取 1~24 的整数。政策/事件类假设通常 3~6 个月；结构性趋势（人口/产业）12~24 个月；
判据里有明确年份的，按距今最近的那个时间点折算月数。

假设列表(JSON)：
{hyps_json}

严格只输出 JSON 数组（不要 markdown）：
[{{"id":"原id","months":6,"reason":"一句话依据(≤20字)"}}]"""


def _need_refill(h, cutoff=None):
    """空 deadline 或晚于动态截止线（今天+MAX_MONTHS 再留 1 个月余量）的都算不可用"""
    due = str(h.get("deadline") or "").strip()
    if not due:
        return True
    if cutoff is None:
        cutoff = (_add_months(datetime.now(), MAX_MONTHS + 1)).strftime("%Y-%m-%d")
    return due > cutoff


def _add_months(base, months):
    """月份加法（钳制到月末，避免 1/31 + 1mo 报错）"""
    y = base.year + (base.month - 1 + months) // 12
    m = (base.month - 1 + months) % 12 + 1
    # 逐月回退找合法日
    for d in (base.day, 28, 15, 1):
        try:
            return datetime(y, m, d)
        except ValueError:
            continue
    return base


def main():
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    todo = [h for h in hyps if h.get("status") != "falsified" and _need_refill(h)]
    print(f"[DEADLINE] 待回填: {len(todo)}/{len(hyps)}")
    if not todo:
        return
    deadline_ts = time.time() + DEADLINE_SECONDS
    today = datetime.now()
    filled = fallback = 0
    for i in range(0, len(todo), BATCH):
        if time.time() > deadline_ts:
            print(f"[DEADLINE] AI 预算用尽, 剩余 {len(todo)-i} 条走 level 兜底")
            for h in todo[i:]:
                months = LEVEL_DEFAULT_MONTHS.get(h.get("level"), 6)
                h["deadline"] = _add_months(today, months).strftime("%Y-%m-%d")
                h["deadline_source"] = "level_default"
                fallback += 1
            break
        batch = todo[i:i + BATCH]
        slim = [{"id": h["id"], "level": h.get("level"), "title": h.get("title"),
                 "rationale": (h.get("rationale") or "")[:150],
                 "falsification": (h.get("falsification_criteria") or "")[:250],
                 "indicators": [(ind.get("name"), ind.get("threshold_refute"))
                                for ind in (h.get("indicators") or [])[:3] if isinstance(ind, dict)]}
                for h in batch]
        by_id = {}
        try:
            results = parse_json_array(call_ai(
                {"api": yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml")
                                       .read_text(encoding="utf-8")).get("api", {})},
                PROMPT_TMPL.format(n=len(batch), hyps_json=json.dumps(slim, ensure_ascii=False))))
            by_id = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}
        except Exception as e:
            print(f"[DEADLINE] batch {i} AI failed: {e} → level 兜底")
        for h in batch:
            months = None
            r = by_id.get(h["id"])
            if r:
                try:
                    months = int(re.search(r"\d+", str(r.get("months"))).group())
                except (AttributeError, ValueError):
                    months = None
                if months is not None and not (1 <= months <= MAX_MONTHS):
                    months = max(1, min(MAX_MONTHS, months or 0))
            if months is None:
                months = LEVEL_DEFAULT_MONTHS.get(h.get("level"), 6)
                h["deadline_source"] = "level_default"
                fallback += 1
            else:
                h["deadline_source"] = "ai_proposed"
                filled += 1
            h["deadline"] = _add_months(today, months).strftime("%Y-%m-%d")

    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DEADLINE] 完成: AI 提议 {filled} 条 + level 兜底 {fallback} 条 -> {HYP_FILE}")
    # 自检：全部节点应有落在 [今年, 今年+3年] 内的 deadline
    bad = [h["id"] for h in hyps if h.get("status") != "falsified" and _need_refill(h)]
    print(f"[DEADLINE] 自检: 仍不可用 {len(bad)} 条 {bad[:5]}")


if __name__ == "__main__":
    main()
