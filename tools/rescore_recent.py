# -*- coding: utf-8 -*-
r"""rescore_recent.py - 关键词表扩充后的一次性存量回填（2026-10-06）

背景：sources.yaml 词表 2026-10-06 从 153 扩到 304 词（补市场/地缘/科技基础词，
修"中文世界要闻命中不了任何词 → base_score=0 → 被误判无关"）。但 base_score 是
采集时写入并持久化的，新词表只对**此后新采**条目生效——存量条目分数仍旧，导致
优先级排序在过渡期内对存量无效。

**严格增量原则（关键）**：jsonl 里 source 字段只存 "rss"、拿不到每条的真实源权重
（实测源权重 0.7~1.3），无法忠实重算 base_score = 源权重 × kw。因此本脚本
**只补 base_score 恰为 0 的条目**（0 → 正分，纯增加），**绝不动已有正分条目**
（避免把对的分数改坏）。补的值取 1.0 × kw（中性基线）。

范围：默认近 7 天（--days），不动更早历史。

用法：
  python tools/rescore_recent.py --days 7 --dry     # 只看会改多少条
  python tools/rescore_recent.py --days 7           # 实际写回（自动备份）

安全：写回前每文件复制为 .bak_rescore_<时间戳>；只改 base_score/keywords_hit。
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(r"D:\osint")
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "cloud"))

import yaml  # noqa: E402
from fetch_rss import RSSFetcher  # noqa: E402

DATA = PROJECT / "data"


def main():
    ap = argparse.ArgumentParser(description="按新词表补齐近期零分条目的 base_score")
    ap.add_argument("--days", type=int, default=7, help="回填窗口（天），默认 7")
    ap.add_argument("--dry", action="store_true", help="只统计不写盘")
    args = ap.parse_args()

    src = yaml.safe_load((PROJECT / "sources.yaml").read_text(encoding="utf-8"))
    fetcher = RSSFetcher([], src.get("keyword_weights") or {}, src.get("keyword_rules") or {})

    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y%m%d")
    files = sorted(DATA.glob("intel_2*.jsonl"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    tot_items = changed = tot_files = 0
    for fp in files:
        date = fp.name.replace("intel_", "").replace(".jsonl", "")
        if not date.isdigit() or len(date) != 8 or date < cutoff:
            continue
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        out = []
        file_changed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                x = json.loads(line)
            except Exception:
                out.append(line)
                continue
            tot_items += 1
            title = x.get("title") or ""
            # 严格增量：只补零分条目；已有正分一律不动
            if title and (x.get("base_score") or 0) == 0:
                content = (x.get("content_preview") or x.get("content") or "")[:600]
                hits, kw = fetcher._calc_keyword_score(title + " " + content)
                if kw > 0:
                    x["keywords_hit"] = hits
                    x["base_score"] = round(kw, 3)   # 中性权重 1.0（源权重不可复原）
                    file_changed += 1
            out.append(json.dumps(x, ensure_ascii=False))
        if file_changed:
            changed += file_changed
            tot_files += 1
            if not args.dry:
                shutil.copy2(fp, fp.with_name(fp.name + ".bak_rescore_" + stamp))
                fp.write_text("\n".join(out) + "\n", encoding="utf-8")

    print(f"窗口: {cutoff} 起（{args.days} 天）")
    print(f"扫描条目 {tot_items} 条，补分 {changed} 条，涉及 {tot_files} 个文件")
    if args.dry:
        print("[dry-run] 未写盘")
    else:
        print(f"已写回（备份后缀 .bak_rescore_{stamp}）")


if __name__ == "__main__":
    main()
