"""政策追踪器 (read-macro 模块 D 下沉, 简化版)
每周聚合 analysis_*.jsonl 中命中政策关键词的条目, 按积累制度维度分组,
调 AI 生成一张"政策观察观点卡", 走 kb_linker.link_view_card_to_kb 落 Obsidian。

复用现有链路: analysis JSONL (main_local 产物) + MacroAnalyzer + kb_linker。
不新起炉灶做产业政策识别——只做关键词聚合 + AI 二次过滤 + 观点卡浓缩。

用法:
  python D:\\osint\\local\\policy_tracker.py --week          # 生成当周 view_card
  python D:\\osint\\local\\policy_tracker.py --dry-run       # 只统计命中, 不调 AI 不落盘
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(r"D:\osint")
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "local"))

# 政策关键词 (十五五/产业政策/新质生产力等), 命中任意一个即候选
POLICY_KEYWORDS = [
    "十五五", "五年规划", "产业政策", "新质生产力", "专精特新",
    "国务院", "发改委", "工信部", "发改委",
    "财政政策", "货币政策", "降准", "降息", "专项债",
    "以旧换新", "消费补贴", "就业优先", "稳就业",
]

# AI 二次过滤: structural_implication 必须同时含政策信号 + 国家-市场维度标签
SECONDARY_FILTER = re.compile(r"(政策|规划|国家进场|产业|补贴|财政|货币)")


def scan_policy_intel(days=7):
    """扫最近 N 天 analysis_*.jsonl, 返回命中政策的条目列表"""
    cutoff = datetime.now() - timedelta(days=days)
    hits = []
    for fp in sorted(DATA.glob("analysis_2*.jsonl")):
        # 文件名日期过滤 (analysis_YYYYMMDD.jsonl)
        m = re.match(r"analysis_(\d{8})\.jsonl", fp.name)
        if m:
            try:
                file_date = datetime.strptime(m.group(1), "%Y%m%d")
                if file_date < cutoff:
                    continue
            except ValueError:
                pass
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            si = str(rec.get("structural_implication", ""))
            diag = rec.get("macro_diagnosis", {}) or {}
            sms = str(diag.get("state_market_shift", ""))
            # 一次过滤: structural_implication 或 state_market_shift 命中关键词
            text = si + " " + sms
            if not any(kw in text for kw in POLICY_KEYWORDS):
                continue
            # 二次过滤: 必须含政策信号词 (减少误判)
            if not SECONDARY_FILTER.search(text):
                continue
            hits.append({
                "intel_id": rec.get("intel_id", ""),
                "structural_implication": si[:300],
                "state_market_shift": sms,
                "accumulation_node": str(diag.get("accumulation_node", "")),
                "confidence": rec.get("confidence", 0),
            })
    return hits


def group_by_accumulation(hits):
    """按积累制度四环节分组: 生产/实现/分配/再生产"""
    groups = {"production": [], "realization": [], "distribution": [], "reproduction": []}
    for h in hits:
        node = h.get("accumulation_node", "")
        for k in groups:
            if k in node:
                groups[k].append(h)
                break
        else:
            groups["production"].append(h)  # 默认归生产
    return groups


def build_card_via_ai(groups, analyzer):
    """调 AI 生成政策观察观点卡 (8 字段, 复用 dialogue_engine 的 card schema)"""
    # 压缩每个环节的信号为 2 条
    evidence_lines = []
    for node, items in groups.items():
        for it in items[:2]:
            evidence_lines.append(f"[{node}] {it['structural_implication'][:150]}")
    if not evidence_lines:
        return None

    system_prompt = (
        "你是政策观察分析师。基于本周命中政策的情报摘要, 生成一张政策观察观点卡。"
        "输出纯JSON(不要markdown), 字段: "
        "title, core_claim, evidence_basis, verifiable_indicators(数组,3条), "
        "refute_criteria, personal_impact, time_horizon_months(数字)。"
        "core_claim 一句话判断当前政策风向; personal_impact 面向年轻失业劳动者。"
    )
    user_prompt = "本周政策相关情报摘要：\n" + "\n".join(evidence_lines[:10])

    try:
        resp = analyzer._call_api(system_prompt, user_prompt)
        resp = re.sub(r"```json\s*|```\s*", "", resp).strip()
        start, end = resp.find("{"), resp.rfind("}")
        if start < 0 or end < 0:
            return None
        card = json.loads(resp[start:end + 1])
        return card
    except Exception as e:
        print(f"[policy_tracker] AI 生成失败: {e}")
        return None


def scan_policy_intel_from_intel(day_strs):
    """补录路径（2026-10-08）：从 intel_YYYYMMDD.jsonl 的 dims/impact 字段取政策信号。

    **为什么需要这条路径**：政策追踪的原生输入是 `analysis_*.jsonl`（Step2 的 AI
    四维诊断产物）。而 2026-08-31 ~ 09-13 期间 Step2 **从未跑通**（AGENTS.md 记录的
    8-30 起静默挂起，直到 9-21 才修好），这两周的 analysis 文件根本不存在
    → policy_week_202636 / 202637 因此缺失，**不是遗漏，是源数据不存在**。

    但 intel 条目自带 `dims`（同一套四维诊断，由 citizen_impact 写入）与
    `impact` / `graduate_impact`，语义等价，可作替代输入。实测命中量充足：
    636 周 134 条、637 周 473 条。

    与原生路径的差异：原生按 `structural_implication` 匹配，这里按
    `impact + graduate_impact + dims` 匹配（字段名不同、语义同源）。
    """
    hits = []
    for d in day_strs:
        fp = DATA / ("intel_" + d + ".jsonl")
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            dims = rec.get("dims") if isinstance(rec.get("dims"), dict) else {}
            text = " ".join([str(rec.get("impact") or ""),
                             str(rec.get("graduate_impact") or ""),
                             str(dims.get("state_market_shift") or ""),
                             str(dims.get("accumulation_node") or "")])
            if not any(kw in text for kw in POLICY_KEYWORDS):
                continue
            if not SECONDARY_FILTER.search(text):
                continue
            hits.append({
                "intel_id": rec.get("id", ""),
                "structural_implication": (str(rec.get("impact") or "")
                                           + " " + str(rec.get("graduate_impact") or ""))[:300],
                "state_market_shift": str(dims.get("state_market_shift") or ""),
                "accumulation_node": str(dims.get("accumulation_node") or ""),
                "confidence": 0.5,
            })
    return hits


def run_policy_observation(analyzer, kb_vault=None, dry_run=False,
                           day_strs=None, week_id=None):
    """主入口: 扫描 → 分组 → AI 观点卡 → 落 KB

    day_strs/week_id 为补录参数（2026-10-08）：给定则走 intel 替代输入路径，
    并按指定周次命名产物（原生路径用当周，无法回溯）。
    """
    if day_strs:
        hits = scan_policy_intel_from_intel(day_strs)
        print(f"[policy_tracker] 补录模式: {len(day_strs)} 天, 命中 {len(hits)} 条政策相关情报")
    else:
        hits = scan_policy_intel(days=7)
        print(f"[policy_tracker] 最近7天命中 {len(hits)} 条政策相关情报")
    if not hits:
        print("[policy_tracker] 无命中, 跳过")
        return None
    groups = group_by_accumulation(hits)
    for node, items in groups.items():
        print(f"  {node}: {len(items)} 条")

    if dry_run:
        print("[policy_tracker] dry-run 模式, 不生成卡片")
        return None

    card = build_card_via_ai(groups, analyzer)
    if not card:
        print("[policy_tracker] AI 未产出卡片")
        return None

    # 归一化 verifiable_indicators: AI 偶尔返回 list of dict, 扁平化
    raw_ind = card.get("verifiable_indicators", [])
    flat_ind = []
    for it in raw_ind:
        if isinstance(it, str):
            flat_ind.append(it)
        elif isinstance(it, dict):
            flat_ind.append(f"{it.get('name', it.get('indicator', '?'))}: {it.get('threshold_support', '')} / {it.get('threshold_refute', '')}")
        elif isinstance(it, list):
            flat_ind.extend(str(x) for x in it)
    if not flat_ind:
        flat_ind = ["(AI 未产出)"]

    # week_id 可由调用方指定（补录场景需回溯到真实周次；原生路径用当周）
    week_id = week_id or datetime.now().strftime("%Y%W")
    card_out = {
        "id": f"policy_week_{week_id}",
        "title": card.get("title", f"政策观察 第{week_id}周"),
        # created 保留生成时刻，另加 covered_range 标明实际覆盖区间（补录时二者不同）
        "created": datetime.now().strftime("%Y-%m-%d"),
        "core_claim": card.get("core_claim", ""),
        "evidence_basis": card.get("evidence_basis", ""),
        "verifiable_indicators": flat_ind,
        "refute_criteria": card.get("refute_criteria", ""),
        "personal_impact": card.get("personal_impact", ""),
        "time_horizon_months": card.get("time_horizon_months", 6),
    }
    if day_strs:
        card_out["backfilled"] = True
        card_out["covered_range"] = "%s ~ %s" % (day_strs[0], day_strs[-1])

    if kb_vault:
        from kb_linker import link_view_card_to_kb
        page = link_view_card_to_kb(card_out, kb_vault)
        print(f"[policy_tracker] 观点卡落盘: {page}")
    else:
        # 无 vault 时落本地 json 备查
        out = DATA / "policy_observation_latest.json"
        out.write_text(json.dumps(card_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[policy_tracker] (无 vault) 落 {out}")
    return card_out


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", action="store_true", help="生成当周政策观察卡")
    parser.add_argument("--dry-run", action="store_true", help="只统计命中, 不调 AI")
    # 补录（2026-10-08）：原生输入 analysis_*.jsonl 缺失的历史周，用 intel 替代输入
    parser.add_argument("--backfill-start", help="补录起始日 YYYYMMDD")
    parser.add_argument("--backfill-end", help="补录结束日 YYYYMMDD")
    parser.add_argument("--week-id", help="补录产物的周次 ID（如 202636）")
    args = parser.parse_args()

    if args.backfill_start and args.backfill_end:
        from datetime import datetime as _dt, timedelta as _td
        s = _dt.strptime(args.backfill_start, "%Y%m%d")
        e = _dt.strptime(args.backfill_end, "%Y%m%d")
        days = []
        while s <= e:
            days.append(s.strftime("%Y%m%d"))
            s += _td(days=1)
        import yaml
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        from load_knowledge import load_knowledge
        from analyze import MacroAnalyzer
        kb = load_knowledge(str(Path(r"D:\Codex输出\视频知识库")))
        try:
            kb.load_all()
        except Exception:
            pass
        analyzer = MacroAnalyzer(config, "", kb)
        if args.dry_run:
            run_policy_observation(None, dry_run=True, day_strs=days)
            return
        run_policy_observation(analyzer, kb_vault=str(Path(r"D:\Codex输出\视频知识库")),
                               day_strs=days, week_id=args.week_id)
        return

    if not (args.week or args.dry_run):
        parser.print_help()
        return

    if args.dry_run:
        run_policy_observation(analyzer=None, dry_run=True)
        return

    # 完整模式: 需要 analyzer + KB vault
    import yaml
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    from load_knowledge import load_knowledge
    from analyze import MacroAnalyzer
    kb = load_knowledge(str(Path(r"D:\Codex输出\视频知识库")))
    kb.load_all()
    analyzer = MacroAnalyzer(config, "", kb)
    run_policy_observation(analyzer, kb_vault=str(Path(r"D:\Codex输出\视频知识库")))


if __name__ == "__main__":
    main()
