"""A 股/港股/美股 PE 估值面板数据源（multpl.com + GuruFocus）
- 3 个指数: S&P 500 PE-TTM (multpl), CSI 300 PE-TTM (GuruFocus), Hang Seng PE-TTM (GuruFocus)
- 拉近 10 年月度数据 → 算 percentile_10y (当前值在 10 年序列中的位置)
- 落 D:\\osint\\data\\index_valuation.json

数据获取模式: agent 自身不调外部 HTTP / subprocess (Mimosa SSRF + 命令注入拦截)
- 数据由 firecrawl MCP (zcode 工具) 提前落盘到 data/raw_valuation_*.md
- 脚本只负责 parse + 算 percentile + 写盘
- 也支持把已 firecrawl 抓的 markdown 喂入

用法:
  # 1. 在 zcode 里调 firecrawl MCP 抓 3 个页面, 输出 markdown 落盘
  # 2. python D:\\osint\\tools\\fetch_index_valuation.py --fetch D:\\osint\\data\\raw_sp500_pe.md us_sp500_pe multpl
  # 3. python D:\\osint\\tools\\fetch_index_valuation.py --calc   # 重新算 percentile 写盘 (无外部调用)
"""
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(r"D:\osint")
OUT = ROOT / "data" / "index_valuation.json"

# 3 个白名单 URL (硬编码, 不暴露给命令行)
URL_SP500 = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
URL_CSI300 = "https://www.gurufocus.com/economic_indicators/4569/pe-ratio-ttm-for-the-csi-300-index"
URL_HSI = "https://www.gurufocus.com/economic_indicators/5732/pe-ratio-ttm-for-the-hang-seng-index"

INDICATORS = [
    {"id": "us_sp500_pe", "name": "S&P 500 PE-TTM", "url": URL_SP500,
     "source": "multpl.com", "currency": "USD", "note": "trailing twelve month", "parser": "multpl"},
    {"id": "cn_csi300_pe", "name": "CSI 300 PE-TTM", "url": URL_CSI300,
     "source": "GuruFocus", "currency": "CNY", "note": "trailing twelve month", "parser": "gurufocus"},
    {"id": "hk_hangseng_pe", "name": "Hang Seng PE-TTM", "url": URL_HSI,
     "source": "GuruFocus", "currency": "HKD", "note": "trailing twelve month", "parser": "gurufocus"},
]


def parse_multpl_pe(md):
    """multpl.com S&P 500 PE 解析 - 表格 '| Aug 31, 2026 | †<br>29.63 |'"""
    pattern = re.compile(
        r"\|\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*\|[^|]*?(\d+\.?\d*)\s*\|"
    )
    rows = pattern.findall(md)
    out = []
    for date_str, val_str in rows:
        try:
            dt = datetime.strptime(date_str, "%b %d, %Y")
            out.append({"date": dt.strftime("%Y-%m"), "value": float(val_str)})
        except ValueError:
            pass
    return out


def parse_gurufocus_pe(md):
    """GuruFocus PE 解析 - 表格 '| 2026-09-01 | 14.1 | -2.76% |'"""
    pattern = re.compile(
        r"\|\s*(\d{4}-\d{2}-\d{1,2})\s*\|\s*(\d+\.?\d*)\s*\|"
    )
    rows = pattern.findall(md)
    out = []
    for date_str, val_str in rows:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            out.append({"date": dt.strftime("%Y-%m"), "value": float(val_str)})
        except ValueError:
            pass
    return out


PARSERS = {"multpl": parse_multpl_pe, "gurufocus": parse_gurufocus_pe}


def percentile_rank(history, current):
    if not history or current is None:
        return None
    sorted_h = sorted(history)
    below = sum(1 for v in sorted_h if v < current)
    return round(below / len(sorted_h), 3)


def points_to_record(ind, points):
    if not points:
        return {"id": ind["id"], "name": ind["name"], "stale": True,
                "stale_reason": "parse 0 points", "source": ind["source"]}
    points.sort(key=lambda p: p["date"], reverse=True)
    current = points[0]
    try:
        cutoff_year = int(current["date"][:4]) - 10
        points_10y = [p for p in points if int(p["date"][:4]) >= cutoff_year]
    except (ValueError, IndexError):
        points_10y = points
    return {
        "id": ind["id"],
        "name": ind["name"],
        "value": current["value"],
        "date": current["date"],
        "currency": ind["currency"],
        "source": ind["source"],
        "source_url": ind["url"],
        "percentile_10y": percentile_rank([p["value"] for p in points_10y], current["value"]),
        "history_count": len(points),
        "history_10y_count": len(points_10y),
        "stale": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", nargs=3, metavar=("MD_FILE", "ID", "SOURCE"),
                        help="从已有 markdown 文件解析并写盘")
    parser.add_argument("--calc", action="store_true",
                        help="仅重算 percentile (复用现有 index_valuation.json)")
    args = parser.parse_args()

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": "PE-TTM (trailing twelve month), percentile over 10y monthly series",
        "indicators": {},
    }

    if args.fetch:
        md_file, ind_id, source = args.fetch
        ind = next((i for i in INDICATORS if i["id"] == ind_id), None)
        if not ind:
            print(f"unknown id {ind_id}")
            sys.exit(1)
        md = Path(md_file).read_text(encoding="utf-8", errors="replace")
        parser_fn = PARSERS.get(source, parse_gurufocus_pe)
        points = parser_fn(md)
        # 合并已有数据 (避免后续 --fetch 覆盖前面的)
        prev = {}
        if OUT.exists():
            try:
                prev = json.loads(OUT.read_text(encoding="utf-8")).get("indicators", {})
            except Exception:
                pass
        prev[ind_id] = points_to_record(ind, points)
        result["indicators"] = prev
        print(f"parsed {len(points)} points for {ind_id}")
    elif args.calc:
        if OUT.exists():
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            result["indicators"] = prev.get("indicators", {})
            for ind_id, rec in result["indicators"].items():
                if rec.get("history"):
                    rec["percentile_10y"] = percentile_rank(rec["history"], rec.get("value"))
        print("calc mode: 重新算 percentile 写盘")
    else:
        # 默认模式: 提示用户用 firecrawl MCP 抓 + --fetch 喂入
        print("ERROR: 需先用 firecrawl MCP 抓 3 个 URL 落 markdown, 再用 --fetch 喂入")
        print("示例:")
        print("  firecrawl scrape https://www.multpl.com/s-p-500-pe-ratio/table/by-month -o D:/osint/data/raw_sp500_pe.md")
        print("  python tools/fetch_index_valuation.py --fetch D:/osint/data/raw_sp500_pe.md us_sp500_pe multpl")
        print("  python tools/fetch_index_valuation.py --fetch D:/osint/data/raw_csi300_pe.md cn_csi300_pe gurufocus")
        print("  python tools/fetch_index_valuation.py --fetch D:/osint/data/raw_hsi_pe.md hk_hangseng_pe gurufocus")
        print("  python tools/fetch_index_valuation.py --calc")
        sys.exit(1)

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[valuation] Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
