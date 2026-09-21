#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 主入口
双击运行：加载情报 -> AI深度分析 -> 生成三形态产物 -> 入库
"""

import sys
import os
import re
import time
import tempfile as _tempfile
import yaml
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from load_intel import load_intel, load_persona
from load_knowledge import load_knowledge
from analyze import analyze_intel
from render_brief import render_brief
from render_dashboard import render_dashboard
from render_wiki import render_wiki


def _fresh_count(items, days=3):
    """统计 published_at 落在近 N 天内的条目数（返回 (fresh_count, newest_past_date)）。

    2026-09-21 踩坑：最初实现取 max(published_at) 算"最新距今几天"，
    但语料里混着源站错误时间戳的未来条目（实测有 2026-11-17），
    max 恒取到未来日期 → 年龄为负 → 闸门永远通过，形同虚设。
    改为**计数**：未来日期天然不落在 [今天-N, 今天] 区间内，不会污染判定。
    只看"有没有足量新鲜条目"，而不是"最极端那条的日期"。
    """
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    lo = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    hi = today.strftime("%Y-%m-%d")
    fresh = 0
    newest_past = ""
    for it in items:
        pub = str(it.get("published_at") or "")[:10]
        if lo <= pub <= hi:
            fresh += 1
            if pub > newest_past:
                newest_past = pub
    return fresh, newest_past


def _wait_for_today_intel(config, output_dir, max_wait_s=600, interval_s=30):
    """等当日情报文件就绪（周任务与 refresh 抢跑时的兜底）。

    根因：OsintWeekly 周一 09:30 触发，机器不可用时由 StartWhenAvailable 补跑，
    补跑时刻可能恰好落在整点——与每小时 OsintRefresh 只差数秒启动，
    此时 refresh 的 git pull 尚未落地，当日文件不存在，Step2 就会退到陈旧缓存。
    这里显式等待，超时则返回 None 交由调用方决定。"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = Path(output_dir) / f"intel_{today}.jsonl"
    waited = 0
    while waited < max_wait_s:
        if target.exists() and target.stat().st_size > 0:
            print(f"    当日情报文件已就绪: {target.name} "
                  f"({target.stat().st_size / 1024:.0f} KB)")
            return target
        print(f"    等待当日情报文件 {target.name} ... ({waited}s/{max_wait_s}s)")
        time.sleep(interval_s)
        waited += interval_s
    print(f"    等待超时（{max_wait_s}s），当日文件仍未就绪")
    return None


def _wait_for_ai_quota_free(max_wait_s=1800, interval_s=60):
    """等 refresh 的 AI 密集步骤让出配额。

    2026-09-21：根因是**配额争抢**而非通道故障。实测 18:35 Step2 启动、
    18:36 refresh 跑完 impact_now（单轮 35 次 AI 调用）期间，Step2 全程收到
    dots HTTP 403；而同一时刻用同一 key 的独立进程测试全部 200。
    refresh 的 impact_now 会写 osint_ai_heavy.lock，这里轮询等它释放。
    拿不到就继续（让 AI 层自己的降级链处理），不无限阻塞。
    """
    lock = Path(_tempfile.gettempdir()) / "osint_ai_heavy.lock"
    if not lock.exists():
        return True
    waited = 0
    while waited < max_wait_s:
        if not lock.exists():
            print(f"    AI 配额已让出（等待 {waited}s）")
            return True
        print(f"    等待 AI 配额释放（refresh 密集步骤占用中，已等 {waited}s）")
        time.sleep(interval_s)
        waited += interval_s
    print(f"    等待 AI 配额超时（{max_wait_s}s），继续执行（靠降级链兜底）")
    return False


def main():
    print("=" * 60)
    print("本地参谋长 - OSINT 个人智库系统")
    print("=" * 60)
    
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    
    paths = config.get("paths", {})
    output_dir = paths.get("output_dir", r"D:\osint\data")
    cache_dir = paths.get("intel_cache_dir", r"D:\osint\data\cache")
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir, "wiki").mkdir(parents=True, exist_ok=True)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir, "logs").mkdir(parents=True, exist_ok=True)
    
    print("\n[1/6] 加载用户画像...")
    persona_path = Path(__file__).parent.parent / "persona.md"
    persona = load_persona(str(persona_path))
    print(f"    画像加载完成 ({len(persona)} 字符)")
    
    print("\n[2/6] 加载知识库...")
    vault_path = config.get("knowledge_base", {}).get("vault_path", r"D:\Codex输出\视频知识库")
    kb = load_knowledge(vault_path)
    idx = kb.load_all()
    nodes_count = idx["total_nodes"]
    macro_count = len(idx["macro_concepts"])
    print(f"    知识库加载完成: {nodes_count} 个节点")
    print(f"    宏观概念: {macro_count} 个")
    
    print("\n[3/6] 加载情报数据...")
    # 先等当日文件就绪（与 refresh 抢跑时，git pull 未落地会导致读到旧数据）
    if not _wait_for_today_intel(config, output_dir):
        print("    当日情报不可用，退出（宁可不跑，不用陈旧数据）")
        return 1

    intel_items = load_intel(config)
    if not intel_items:
        # 2026-09-21 修复：原实现 max(cache_files, key=mtime) 有两个坑——
        # ① 周任务与 refresh 抢跑时（间隔 3 秒），当日产物文件尚未落地，
        #    这里会抓到任意旧快照（实测抓到 9-14 的 1006 条，与当日仅 4 条交集）；
        # ② mtime 排序会把 main_local 自己刚写的当日空壳快照排在前面。
        # 改为按文件名日期取最新，再叠加内容新鲜度闸门。
        MAX_CACHE_AGE_DAYS = 2
        dated = []
        for fp in Path(cache_dir).glob("intel_*.jsonl"):
            m = re.match(r"intel_(\d{8})\.jsonl$", fp.name)
            if m:
                dated.append((m.group(1), fp))
        if dated:
            dated.sort(key=lambda t: t[0], reverse=True)
            newest_date, newest_path = dated[0]
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            age_days = (datetime.strptime(today, "%Y%m%d")
                        - datetime.strptime(newest_date, "%Y%m%d")).days
            if age_days > MAX_CACHE_AGE_DAYS:
                print(f"    最新缓存 {newest_path.name} 已过期 {age_days} 天"
                      f"（上限 {MAX_CACHE_AGE_DAYS} 天），拒绝用陈旧数据")
                print("    请先跑 refresh.py 拉取当日情报后重试")
                return 1
            print(f"    找到缓存: {newest_path}（{age_days} 天前）")
            for line in newest_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    intel_items.append(json.loads(line))

    if not intel_items:
        print("    无可用情报数据，退出")
        return 1

    all_intel = intel_items          # 全量：写入缓存快照，保持当日数据的完整镜像
    intel_count = len(intel_items)
    print(f"    加载情报: {intel_count} 条")

    # 内容新鲜度闸门：文件名日期不可信（旧数据会被写进当日命名的缓存），
    # 必须按 published_at 判断，超期则拒绝分析，避免污染 analysis_*.jsonl。
    # 用"近 N 天条目数"而非"最新一条距今几天"——后者会被未来日期脏数据绕过。
    MAX_CONTENT_AGE_DAYS = int(config.get("ai_analysis", {}).get("max_content_age_days", 3))
    MIN_FRESH_ITEMS = int(config.get("ai_analysis", {}).get("min_fresh_items", 5))
    fresh_n, newest_past = _fresh_count(intel_items, MAX_CONTENT_AGE_DAYS)
    if fresh_n < MIN_FRESH_ITEMS:
        print(f"    近 {MAX_CONTENT_AGE_DAYS} 天内仅 {fresh_n} 条情报"
              f"（下限 {MIN_FRESH_ITEMS} 条），数据陈旧，拒绝分析")
        print("    请先跑 refresh.py 拉取当日情报后重试")
        return 1
    print(f"    内容新鲜度: 近 {MAX_CONTENT_AGE_DAYS} 天 {fresh_n} 条"
          f"（最新 {newest_past or 'N/A'}）")

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    cache_file = Path(cache_dir) / f"intel_{date_str}.jsonl"
    cache_file.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in all_intel) + "\n",
        encoding="utf-8")
    print(f"    缓存已更新: {cache_file}")

    # 2026-09-21 修复：AI 分析条数上限。此前全量送入（实测 1006 条 × batch_size 10
    # = 101 批，唯一可用通道 dots 实测 137s/批 → 需 3.8 小时，Step2 自 8-30 起从未跑完）。
    # 按 final_score 降序取 Top N（同分按发布时间新的优先），默认 60 条约 14 分钟。
    ai_cfg = config.get("ai_analysis", {})
    max_items = int(ai_cfg.get("max_items", 60))
    if intel_count > max_items:
        def _rank_key(it):
            try:
                score = float(it.get("final_score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            return (score, str(it.get("published_at") or ""))
        intel_items = sorted(intel_items, key=_rank_key, reverse=True)[:max_items]
        print(f"    按 final_score 取 Top {max_items} 条进入 AI 分析"
              f"（跳过 {intel_count - max_items} 条，缓存快照仍为全量 {intel_count} 条）")
    
    print("\n[4/6] AI 深度分析（四维政治经济学框架）...")
    # 先等 refresh 的 AI 密集步骤（impact_now/translate）让出配额，避免争抢触发 403
    _wait_for_ai_quota_free()
    analyses = analyze_intel(config, persona, kb, intel_items)
    analysis_count = len(analyses)
    print(f"    分析完成: {analysis_count} 条")
    
    analysis_file = Path(output_dir) / f"analysis_{date_str}.jsonl"
    analysis_file.write_text(
        "\n".join(json.dumps(a.__dict__ if hasattr(a, "__dict__") else a, ensure_ascii=False) for a in analyses) + "\n",
        encoding="utf-8")
    print(f"    分析结果已保存: {analysis_file}")
    
    print("\n[5/6] 生成产物...")
    
    brief_cfg = config.get("output", {}).get("brief", {})
    if brief_cfg.get("enabled", True):
        print("    生成每日简报...")
        render_brief(analyses, intel_items, date_str, output_dir, config)
    
    dash_cfg = config.get("output", {}).get("dashboard", {})
    if dash_cfg.get("enabled", True):
        print("    生成交互式仪表盘...")
        render_dashboard(analyses, intel_items, date_str, output_dir, config)
    
    wiki_cfg = config.get("output", {}).get("wiki", {})
    if wiki_cfg.get("enabled", True):
        print("    生成 Obsidian 知识库页面...")
        render_wiki(analyses, intel_items, date_str, config)
    
    print("\n[6/6] 完成!")
    print("=" * 60)
    print(f"输出目录: {output_dir}")
    print(f"  简报: brief_{date_str}.md")
    print(f"  仪表盘: dashboard_{date_str}.html")
    print(f"  知识库: wiki/osint-{date_str}.md + 宏观概念页")
    print(f"  分析明细: analysis_{date_str}.jsonl")
    print("=" * 60)
    print("\n使用建议:")
    print("  1. 双击 brief_*.md 在 Obsidian/浏览器阅读每日内参")
    print("  2. 双击 dashboard_*.html 打开交互式仪表盘筛选钻取")
    print("  3. 在 Obsidian 中查看 wiki/ 目录下的新页面")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
