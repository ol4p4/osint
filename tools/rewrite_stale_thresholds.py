# -*- coding: utf-8 -*-
r"""rewrite_stale_thresholds.py - 过期判据重写（2026-09-21）

**背景**：`_decompose_view` 的 prompt 原先未告知模型当前日期，AI 凭训练数据
时间感写年份，导致 75 个节点中 59 个（78%）的证伪判据含过期年份（2020-2025）。
更严重的是**判据里编造了具体统计数值**（如"2025年补贴占比从2024年3.5%降至3.2%"），
却挂着"根据财政部数据"的名头——验证时会与真实数据冲突。

**只处理 due_date 未到期的节点**（默认 2027+）。due_date 已到的 18 个节点
**必须走验证流程**——重写会把本该被验证的假设永久推迟（见 AGENTS.md）。

**做法**：用已修复的 prompt（注入当前日期 + 禁编造统计 + 未知基线用相对表述）
重新生成 indicators / sub_propositions 的阈值，并同步更新 falsification_criteria。
保留旧值到 `_prev_thresholds` 供对比回滚。

用法：
    python tools/rewrite_stale_thresholds.py --dry              # 列清单
    python tools/rewrite_stale_thresholds.py --limit 5          # 小批试跑
    python tools/rewrite_stale_thresholds.py --all              # 全量
    python tools/rewrite_stale_thresholds.py --all --no-apply   # 只生成不写盘
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
sys.path.insert(0, str(PROJECT / "cloud"))


def years_in(text):
    return sorted({int(y) for y in re.findall(r"(20[1-9]\d)", str(text or ""))})


# 只检查"判定年份"字段——source 里出现的年份是数据源版本号
# （如「UN, World Population Prospects 2024」），不是判据时间，扫描它会误报
JUDGMENT_FIELDS = ("threshold_support", "threshold_refute")


def stale_in_indicators(inds, current_year):
    """扫描指标的判定年份字段，返回早于今年的年份列表。

    2026-09-21 踩坑：首版对整个 indicator 做正则，把 source 里的版本号
    （World Population Prospects 2024）当成过期判据误报，导致误判重写失败。
    """
    bad = []
    for ind in (inds or []):
        if not isinstance(ind, dict):
            continue
        for k in JUDGMENT_FIELDS:
            for y in years_in(ind.get(k)):
                if y < current_year:
                    bad.append(y)
    return sorted(set(bad))


# 疑似编造的数据源特征：《...报告/公报/年鉴/白皮书/蓝皮书...》里带年份
# 实测 AI 会写「中国国家统计局《2026 年全国统计公报》」这类不存在的出版物。
# 真实数据源只写机构名（IMF WEO / UN WPP / ILOSTAT / 各国统计局）即可。
# 正则需容忍年份出现在书名号内任意位置（《2026 年全国统计公报》vs《青年就业报告 2026》）。
FABRICATED_SOURCE_RE = re.compile(
    r"《[^》]{0,40}(报告|公报|年鉴|白皮书|蓝皮书)[^》]{0,20}》|"
    r"《[^》]{0,20}\d{4}[^》]{0,30}(报告|公报|年鉴|白皮书|蓝皮书)[^》]{0,20}》")

# 区域表述：东亚比较只应有中日韩
FORBIDDEN_REGION_TERMS = ("台湾", "香港", "澳门")


def quality_issues(inds, current_year):
    """返回质量问题列表（编造数据源 / 违规区域表述 / 过期判定年份）。

    2026-09-21：AI 把 prompt 里"数据源名称可保留原文"理解为可以编造报告名，
    产出了「中国国家统计局《2026 年全国统计公报》」（2026 年公报尚不存在）。
    数值编造容易发现，编造一个听起来合理的报告名很难察觉，故加自动检测。
    """
    issues = []
    for ind in (inds or []):
        if not isinstance(ind, dict):
            continue
        src = str(ind.get("source") or "")
        if FABRICATED_SOURCE_RE.search(src):
            issues.append("疑似编造数据源: " + src[:40])
        for k in JUDGMENT_FIELDS:
            v = str(ind.get(k) or "")
            for term in FORBIDDEN_REGION_TERMS:
                if term in v:
                    issues.append("区域表述含「" + term + "」: " + v[:40])
        for y in stale_in_indicators([ind], current_year):
            issues.append("过期判定年份 " + str(y))
    return issues


def due_of(h):
    """树节点用 deadline，engine 节点用 due_date（字段不统一，见 AGENTS.md）"""
    return (h.get("deadline") or h.get("due_date") or "")[:10]


def build_prompt(h, now):
    """与 hypothesis_engine._decompose_view 同源的修复版 prompt（中文输出）。

    2026-09-21 教训：首版 prompt 用英文写，生成结果也全英文，与中文仪表盘/知识库
    混杂。改为中文提问 + 明确要求中文输出，字段名保持英文（代码消费）。
    """
    today = now.strftime("%Y-%m-%d")
    horizon = h.get("time_horizon_months", 12)
    due_dt = now + timedelta(days=horizon * 30)
    due = due_dt.strftime("%Y-%m-%d")
    inds = h.get("indicators") or []
    ind_txt = "\n".join(
        "  - " + str(i.get("name", "")) + "（数据源：" + str(i.get("source", "?")) + "）"
        for i in inds if isinstance(i, dict)) or "  （暂无，请提 2-4 个）"

    return (
        "请为下面这个假设重写可证伪的阈值判据。\n\n"
        "**今天是：" + today + "（" + str(now.year) + " 年）**\n"
        "**验证截止日：" + due + "**——所有阈值必须落在这个窗口内。\n\n"
        "假设：" + str(h.get("title", "")) + "\n"
        "依据：" + str(h.get("rationale") or h.get("core_claim") or "")[:400] + "\n"
        "现有指标：\n" + ind_txt + "\n\n"
        "输出 JSON 数组（字段名保持英文，值用中文）：\n"
        '[{"name":"指标名","source":"查证来源","threshold_support":"什么情况支持该假设",'
        '"threshold_refute":"什么情况证伪该假设"}]\n\n'
        "硬性要求：\n"
        "1. **所有文字用中文**（数据源名称、机构名可保留原文）。\n"
        "2. 阈值必须能在 " + today + " 到 " + due + " 之间检验，**绝不能出现 "
        + str(now.year) + " 年之前的判定年份**。\n"
        "3. 每个阈值显式写出年份，例如「到 " + str(due_dt.year) + " 年，X 超过 Y」。\n"
        "4. **禁止编造统计数据**。若某个当前基线值不确定，改用**相对表述**\n"
        "   （例如「较 " + str(now.year) + " 年水平增长 20% 以上」），\n"
        "   绝对不要写一个你无法核实的当前具体数值。\n"
        "5. **数据源只写真实存在的常设机构/数据库**（如 IMF WEO、UN WPP、ILOSTAT、\n"
        "   各国统计局、人社部）。**严禁编造报告名称或年鉴**——\n"
        "   若不确定某报告是否存在，只写机构名（例如「中国国家统计局」），\n"
        "   不要写《XX 报告 " + str(now.year) + "》这种你无法确认的具体出版物。\n"
        "6. 涉及东亚区域比较时，**只写「中国、日本、韩国」三国**，\n"
        "   不要引入台湾、香港等其他经济体。"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-apply", action="store_true", help="只生成不写盘")
    ap.add_argument("--save-every", type=int, default=10)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))

    # 候选：判据含过期年份 且 due_date 未到期（2027+）
    cand, skipped = [], []
    for h in hyps:
        fc = h.get("falsification_criteria") or ""
        stale = [y for y in years_in(fc) if y < now.year]
        if not stale:
            continue
        due = due_of(h)
        due_ys = years_in(due)
        due_year = due_ys[0] if due_ys else None
        if due_year and due_year > now.year:
            cand.append(h)
        else:
            skipped.append((h, due))

    print(f"[REWRITE] 判据含过期年份: {len(cand) + len(skipped)} 个")
    print(f"[REWRITE] 可重写（due_date > {now.year}）: {len(cand)} 个")
    print(f"[REWRITE] 跳过（due_date 已到，须走验证）: {len(skipped)} 个")
    for h, d in skipped[:8]:
        print(f"    - {str(h.get('title',''))[:30]:32s} due={d}")
    if len(skipped) > 8:
        print(f"    ... 另有 {len(skipped) - 8} 个")

    if args.dry or not cand:
        if args.dry:
            print()
            print("--- 待重写清单 ---")
            for h in cand:
                print(f"  [{str(h.get('level') or '?'):6s}] {due_of(h):12s} "
                      f"{str(h.get('title',''))[:34]}")
        return

    if not args.all and not args.limit:
        print("[REWRITE] 需显式 --limit N 或 --all")
        return
    take = cand if args.all else cand[:args.limit]

    import yaml
    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    cfg = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    analyzer = MacroAnalyzer(cfg, "", kb)

    system = ("You are a political economy analyst. Output JSON only. "
              "You are working in the year " + str(now.year) +
              "; never propose thresholds dated before that, and never invent statistics.")

    t0 = time.time()
    done = failed = 0
    for i, h in enumerate(take, 1):
        try:
            raw = analyzer._call_api(system, build_prompt(h, now))
            raw = re.sub(r"```json\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            s, e = raw.find("["), raw.rfind("]")
            if s < 0 or e < 0:
                raise ValueError("AI 未返回 JSON 数组")
            new_inds = json.loads(raw[s:e + 1])
            if not isinstance(new_inds, list) or not new_inds:
                raise ValueError("生成结果为空")

            # 校验：判定年份不得早于今年（只扫 threshold_* 字段，跳过 source 版本号）
            new_inds_norm = [{
                "name": str(ni.get("name", ""))[:120],
                "source": str(ni.get("source", ""))[:80],
                "threshold_support": str(ni.get("threshold_support", ""))[:200],
                "threshold_refute": str(ni.get("threshold_refute", ""))[:200],
            } for ni in new_inds if isinstance(ni, dict)]
            bad = stale_in_indicators(new_inds_norm, now.year)
            qissues = quality_issues(new_inds_norm, now.year)
            # 旧值存档 + 写入
            h["_prev_thresholds"] = {
                "falsification_criteria": h.get("falsification_criteria"),
                "indicators": h.get("indicators"),
                "rewritten_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            h["indicators"] = new_inds_norm
            h["falsification_criteria"] = "；".join(
                x["threshold_refute"] for x in h["indicators"] if x.get("threshold_refute"))[:300]
            h["stale_thresholds"] = bad if bad else []
            if qissues:
                h["quality_issues"] = qissues
            done += 1
            flag = ("  ⚠️ " + "; ".join(qissues[:2])) if qissues else ""
            print(f"  [{i}/{len(take)}] {str(h.get('title',''))[:34]}{flag}")
        except Exception as ex:
            failed += 1
            print(f"  [{i}/{len(take)}] FAIL {type(ex).__name__}: {str(ex)[:80]}")
        if done and done % args.save_every == 0 and not args.no_apply:
            HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"      已落盘（{done} 个）")

    el = time.time() - t0
    print()
    print(f"[REWRITE] 完成 {done}/{len(take)}（失败 {failed}），耗时 {el:.1f}s")
    if args.no_apply:
        print("[REWRITE] --no-apply：未写盘")
        return
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REWRITE] 已写盘 -> {HYP_FILE}")

    # 复核
    still = [h for h in hyps
             if [y for y in years_in(h.get("falsification_criteria")) if y < now.year]]
    print(f"[REWRITE] 复核：仍含过期年份的节点 {len(still)} 个（含跳过的 {len(skipped)} 个）")


if __name__ == "__main__":
    main()
