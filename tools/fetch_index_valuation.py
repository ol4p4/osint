"""A 股/港股/美股 PE 估值面板数据源（multpl.com + GuruFocus）
- 3 个指数: S&P 500 PE-TTM (multpl), CSI 300 PE-TTM (GuruFocus), Hang Seng PE-TTM (GuruFocus)
- 拉近 10 年月度数据 → 算 percentile_10y (当前值在 10 年序列中的位置)
- 落 <ROOT>/data/index_valuation.json

三种数据获取模式:
  --firecrawl  直调 Firecrawl REST API 抓 3 个页面（2026-10-14，CI 无人值守路径）
  --fetch     从已有 markdown 文件解析（本地 agent 用 MCP 抓完喂入）
  --calc      仅重算 percentile（无外部调用）

**为什么加 --firecrawl**（2026-10-14）：此前依赖 zcode 的 firecrawl MCP——那是
本地 agent 专用工具，CI 上不存在，导致估值面板在云端长期为空。MCP 只是包装层，
底层就是 REST API（api.firecrawl.dev，Bearer key），脚本直调即可上云。
key 经 FIRECRAWL_API_KEY 环境变量注入（CI 走 GitHub Secrets）。

根目录解析：OSINT_ROOT 可覆盖（CI 指向 checkout），默认 D:\\osint。

用法:
  python tools/fetch_index_valuation.py --firecrawl   # CI/无人值守：直调 API 抓+解析+写盘
  python tools/fetch_index_valuation.py --fetch <md> <id> <parser>   # 手动喂 markdown
  python tools/fetch_index_valuation.py --calc        # 重新算 percentile 写盘
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone


def _resolve_root():
    """解析项目根目录：OSINT_ROOT 显式覆盖，否则默认 D:\\osint。

    同 gen_dashboard._resolve_root 校验规格（禁 `..`、须绝对路径），
    校验失败退回默认根。
    """
    default = r"D:\osint"
    raw = (os.environ.get("OSINT_ROOT") or "").strip()
    if not raw:
        return Path(default)
    if ".." in raw.replace("\\", "/").split("/"):
        return Path(default)
    p = Path(raw)
    return p if p.is_absolute() else Path(default)


ROOT = _resolve_root()
OUT = ROOT / "data" / "index_valuation.json"

# Firecrawl 直调（2026-10-14）：固定官方端点 + 仅 https，无用户可控 URL 拼接
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
# SSRF 白名单（对齐 fetch_macro_indicators.ALLOWED_HOSTS 规格）
_ALLOWED_SCRAPE_HOSTS = {"api.firecrawl.dev"}
_ALLOWED_TARGET_HOSTS = {"www.multpl.com", "www.gurufocus.com"}

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
    """multpl.com S&P 500 PE 解析
    兼容两种输入：firecrawl markdown 表格 '| Aug 31, 2026 | †<br>29.63 |'
    和直接抓的原始 HTML '<td>Sep 18, 2026</td><td>… 26.07</td>'（2026-09-19 加）
    """
    out = []
    # 先试 markdown 表格格式
    pattern = re.compile(
        r"\|\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*\|[^|]*?(\d+\.?\d*)\s*\|"
    )
    for date_str, val_str in pattern.findall(md):
        try:
            dt = datetime.strptime(date_str, "%b %d, %Y")
            out.append({"date": dt.strftime("%Y-%m"), "value": float(val_str)})
        except ValueError:
            pass
    if out:
        return out
    # HTML 表格格式：td 里的日期 + 其后第一个数字（剥 abbr 标签与 &nbsp; 类实体）
    cell = re.compile(r"<td>(.*?)</td>", re.S)
    cells = [re.sub(r"<[^>]+>", "", c) for c in cell.findall(md)]
    cells = [c.replace("&#x2002;", " ").replace("&nbsp;", " ").replace("†", "").strip() for c in cells]
    pending_date = None
    for c in cells:
        dm = re.match(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})$", c)
        if dm:
            pending_date = dm.group(1)
            continue
        vm = re.match(r"^(\d+\.?\d*)$", c)
        if vm and pending_date:
            try:
                dt = datetime.strptime(pending_date, "%b %d, %Y")
                out.append({"date": dt.strftime("%Y-%m"), "value": float(vm.group(1))})
            except ValueError:
                pass
            pending_date = None
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
    # 历史曲线 (按时间升序, 最多 120 点防 JSON 膨胀)
    history_10y = [{"date": p["date"], "value": p["value"]} for p in reversed(points_10y)][-120:]
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
        "history_10y": history_10y,
        "stale": False,
    }


def _host_is_safe(host):
    """解析 host 的 IP，若属 RFC1918/loopback/link-local 等则拒绝（SSRF 守卫）。"""
    import ipaddress
    import socket
    try:
        for info in socket.getaddrinfo(host, 443):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved):
                return False
    except Exception:
        return False
    return True


def _firecrawl_scrape(url, timeout=60):
    """直调 Firecrawl REST API 抓单个 URL，返回 markdown 文本。

    SSRF 守卫（对齐 fetch_macro_indicators 规格）：仅 https + host 白名单
    + 解析后 IP 边界校验（拒绝私网/环回/链路本地）。端点与目标 URL 均过
    同一守卫——任何未校验 URL 不得进入服务端请求。
    key 从 FIRECRAWL_API_KEY 读（CI 走 GitHub Secrets）。
    """
    from urllib.parse import urlparse

    ep = urlparse(_FIRECRAWL_ENDPOINT)
    if ep.scheme != "https" or ep.hostname not in _ALLOWED_SCRAPE_HOSTS:
        raise RuntimeError(f"端点不在白名单: {_FIRECRAWL_ENDPOINT}")
    if not _host_is_safe(ep.hostname):
        raise RuntimeError(f"端点解析到受限 IP: {ep.hostname}")
    tp = urlparse(url)
    if tp.scheme != "https" or tp.hostname not in _ALLOWED_TARGET_HOSTS:
        raise RuntimeError(f"目标 URL 不在白名单: {url}")
    if not _host_is_safe(tp.hostname):
        raise RuntimeError(f"目标解析到受限 IP: {tp.hostname}")

    api_key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY 未设置")
    body = json.dumps({
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        _FIRECRAWL_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    md = ((payload or {}).get("data") or {}).get("markdown") or ""
    if not md:
        raise RuntimeError(f"firecrawl 返回空 markdown（success={payload.get('success')}）")
    return md


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", nargs=3, metavar=("MD_FILE", "ID", "SOURCE"),
                        help="从已有 markdown 文件解析并写盘")
    parser.add_argument("--firecrawl", action="store_true",
                        help="直调 Firecrawl API 抓 3 个页面（CI 无人值守路径）")
    parser.add_argument("--calc", action="store_true",
                        help="仅重算 percentile (复用现有 index_valuation.json)")
    args = parser.parse_args()

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": "PE-TTM (trailing twelve month), percentile over 10y monthly series",
        "indicators": {},
    }

    if args.firecrawl:
        # 三页逐个抓：单页失败保留旧值；全失败不写盘（防 stale 空壳覆盖）
        prev = {}
        if OUT.exists():
            try:
                prev = json.loads(OUT.read_text(encoding="utf-8")).get("indicators", {})
            except Exception:
                prev = {}
        ok = 0
        for ind in INDICATORS:
            try:
                md = _firecrawl_scrape(ind["url"])
                points = PARSERS.get(ind["parser"], parse_gurufocus_pe)(md)
                if not points:
                    print(f"[valuation] {ind['id']}: parse 0 点，保留旧值")
                    continue
                prev[ind["id"]] = points_to_record(ind, points)
                ok += 1
                print(f"[valuation] {ind['id']}: {len(points)} 点")
            except Exception as e:
                print(f"[valuation] {ind['id']} 失败: {str(e)[:120]}（保留旧值）")
        result["indicators"] = prev
        if ok == 0:
            print("[valuation] 三个页面全部失败，不写盘")
            sys.exit(1)
    elif args.fetch:
        md_file, ind_id, source = args.fetch
        ind = next((i for i in INDICATORS if i["id"] == ind_id), None)
        if not ind:
            print(f"unknown id {ind_id}")
            sys.exit(1)
        md = Path(md_file).read_text(encoding="utf-8", errors="replace")
        parser_fn = PARSERS.get(source, parse_gurufocus_pe)
        points = parser_fn(md)
        if not points:
            # 2026-09-19 修复: parse 0 点时拒绝写盘——此前 stale 空壳覆盖旧指标,
            # 152 点历史被一个坏 HTML 毁掉
            print(f"parse 0 points for {ind_id}, refuse to overwrite existing record")
            sys.exit(1)
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
                # 2026-09-19 修复: 字段名是 history_10y 不是 history(此前 percentile 永不重算)
                hist = rec.get("history_10y") or rec.get("history") or []
                if hist:
                    rec["percentile_10y"] = percentile_rank(
                        [p["value"] if isinstance(p, dict) else p for p in hist],
                        rec.get("value"))
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
