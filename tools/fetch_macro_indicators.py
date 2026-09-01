"""宏观关键指标抓取 - 独立数据集，写 D:\\osint\\data\\macro_indicators.json
只跑免费、无 key 的 API：FRED (DEMO_KEY) + Frankfurter + World Bank
失败项保留 stale 标记 + 上次值，不阻塞。

SSRF 防护：所有 URL 在发出前过白名单 + 解析 IP 检查（拒绝 RFC1918/loopback/link-local）。
"""
import ipaddress
import json
import socket
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlparse

ROOT = Path(r"D:\osint")
OUT = ROOT / "data" / "macro_indicators.json"
TIMEOUT = 10

# 白名单：只允许 https + 这些 host
ALLOWED_HOSTS = {
    "api.stlouisfed.org",
    "fred.stlouisfed.org",
    "api.frankfurter.app",
    "api.worldbank.org",
    "www.stats.gov.cn",
    "data.stats.gov.cn",
    "tradingeconomics.com",
    "economy.caixin.com",
}


def _host_is_safe(host):
    """解析 host IP，若属 RFC1918/loopback/link-local 则拒绝。"""
    try:
        for info in socket.getaddrinfo(host, 443):
            ip_str = info[4][0]
            ip = ipaddress.ip_address(ip_str)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved):
                return False
    except Exception:
        return False
    return True


def _get(url, timeout=TIMEOUT):
    """白名单 + IP 校验的 URL 拉取。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"scheme {parsed.scheme!r} not allowed (https only)")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host {parsed.hostname!r} not in whitelist")
    if not _host_is_safe(parsed.hostname):
        raise ValueError(f"host {parsed.hostname!r} resolves to unsafe IP")
    req = Request(url, headers={"User-Agent": "osint-dashboard/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fetch_fred(series_id):
    """FRED 公开 CSV 端点（无需 key）。"""
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        text = _get(url)
        # 取最后非空数据行（csv: DATE,VALUE）
        last_date = ""
        last_val = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("DATE,") or line.startswith("DATE "):
                continue
            parts = line.split(",")
            if len(parts) >= 2 and parts[1] not in (".", ""):
                last_date = parts[0]
                try:
                    last_val = float(parts[1])
                except ValueError:
                    continue
        if last_val is not None:
            return {
                "value": last_val,
                "date": last_date,
                "source": "FRED",
            }
    except Exception as e:
        return {"error": str(e)[:80], "source": "FRED"}
    return None


def fetch_frankfurter(base, target):
    """汇率。"""
    try:
        url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
        data = json.loads(_get(url))
        rate = data.get("rates", {}).get(target)
        if rate is not None:
            return {
                "value": round(float(rate), 4),
                "date": data.get("date", ""),
                "source": "Frankfurter",
            }
    except Exception as e:
        return {"error": str(e)[:80], "source": "Frankfurter"}
    return None


def fetch_worldbank(country, indicator):
    """世行数据 - 取最近一年。"""
    try:
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json&per_page=3&mrnev=1"
        data = json.loads(_get(url))
        if isinstance(data, list) and len(data) > 1 and data[1]:
            for entry in data[1]:
                v = entry.get("value")
                if v is not None:
                    return {
                        "value": float(v),
                        "date": entry.get("date", ""),
                        "source": "WorldBank",
                    }
    except Exception as e:
        return {"error": str(e)[:80], "source": "WorldBank"}
    return None


def fetch_te_china_indicator(page_slug, label_keyword):
    """Trading Economics 抓取中国宏观指标（同步 NBS 官方数据）。
    TE 同步源标注 "National Bureau of Statistics of China"，数据可信度与 NBS 一致。
    page_slug: URL 路径段，如 "youth-unemployment-rate" 或 "inflation-cpi"
    label_keyword: meta description 里的英文短语（区分 Youth/General Unemployment/Inflation）
    """
    import re
    try:
        te_url = f"https://tradingeconomics.com/china/{page_slug}"
        html = _get(te_url, timeout=15)  # 走白名单+IP安全检查
        m = re.search(
            rf"{re.escape(label_keyword)} in China (?:increased|decreased|rose|fell|was) to ([\d.]+) (?:percent|points) in (\w+)",
            html,
        )
        if m:
            return {
                "value": float(m.group(1)),
                "date": m.group(2),
                "source": "NBS-TE",
            }
    except Exception as e:
        return {"error": str(e)[:80], "source": "NBS-TE"}
    return None


def _parse_caixin_unrate(article_url, page_text):
    """从财新文章 markdown/HTML 解析 4 个分年龄组失业率值"""
    import re
    m16 = re.search(r"16[—\-]24岁[^\d]{0,60}?(\d+\.?\d*)%", page_text)
    m25 = re.search(r"25[—\-]29岁[^\d]{0,60}?(\d+\.?\d*)%", page_text)
    m30 = re.search(r"30[—\-]59岁[^\d]{0,60}?(\d+\.?\d*)%", page_text)
    mtotal = re.search(r"全国城镇调查失业率[^\d]{0,30}?(\d+\.?\d*)%", page_text)
    if not m16:
        return None
    rec = {
        "cn_youth_16_24": float(m16.group(1)),
        "source_url": article_url,
        "source": "NBS-via-Caixin",
    }
    if m25:
        rec["cn_25_29"] = float(m25.group(1))
    if m30:
        rec["cn_30_59"] = float(m30.group(1))
    if mtotal:
        rec["cn_total"] = float(mtotal.group(1))
    um = re.search(r"/(\d{4})-(\d{2})-\d{2}/", article_url)
    if um:
        pub_year, pub_month = int(um.group(1)), int(um.group(2))
        data_month = pub_month - 1
        data_year = pub_year
        if data_month == 0:
            data_month = 12
            data_year -= 1
        rec["date"] = f"{data_year}-{data_month:02d}"
    return rec


# 财新月度报道 URL 离线映射表 (NBS 2023-12 起新口径月度数据)
# 财新 403 urllib 直连, 改走 firecrawl 或手工维护 URL 表
_CAIXIN_URL_TABLE = {
    "2023-12": "https://economy.caixin.com/2024-01-19/102152787.html",
    "2024-01": "https://economy.caixin.com/2024-02-19/102160892.html",
    "2024-02": "https://economy.caixin.com/2024-03-20/102177332.html",
    "2024-03": "https://economy.caixin.com/2024-04-17/102187325.html",
    "2024-04": "https://economy.caixin.com/2024-05-20/102198260.html",
    "2024-05": "https://economy.caixin.com/2024-06-19/102209121.html",
    "2024-06": "https://economy.caixin.com/2024-07-19/102219823.html",
    "2024-07": "https://economy.caixin.com/2024-08-19/102228420.html",
    "2024-08": "https://economy.caixin.com/2024-09-20/102237122.html",
    "2024-09": "https://economy.caixin.com/2024-10-22/102248117.html",
    "2024-10": "https://economy.caixin.com/2024-11-20/102256810.html",
    "2024-11": "https://economy.caixin.com/2024-12-19/102267380.html",
    "2024-12": "https://economy.caixin.com/2025-01-21/102277340.html",
    "2025-01": "https://economy.caixin.com/2025-02-20/102287020.html",
    "2025-02": "https://economy.caixin.com/2025-03-21/102300574.html",
    "2025-03": "https://economy.caixin.com/2025-04-21/102311140.html",
    "2025-04": "https://economy.caixin.com/2025-05-21/102321765.html",
    "2025-05": "https://economy.caixin.com/2025-06-19/102332256.html",
    "2025-06": "https://economy.caixin.com/2025-07-17/102342203.html",
    "2025-07": "https://economy.caixin.com/2025-08-19/102353358.html",
    "2025-08": "https://economy.caixin.com/2025-09-20/102365120.html",
    "2025-09": "https://economy.caixin.com/2025-10-22/102376012.html",
    "2025-10": "https://economy.caixin.com/2025-11-20/102386540.html",
    "2025-11": "https://economy.caixin.com/2025-12-19/102397120.html",
    "2025-12": "https://economy.caixin.com/2026-01-22/102406740.html",
    "2026-01": "https://economy.caixin.com/2026-02-19/102415720.html",
    "2026-02": "https://economy.caixin.com/2026-03-19/102424693.html",
    "2026-03": "https://economy.caixin.com/2026-04-21/102436199.html",
    "2026-04": "https://economy.caixin.com/2026-05-21/102446106.html",
    "2026-05": "https://economy.caixin.com/2026-06-19/102456890.html",
    "2026-06": "https://economy.caixin.com/2026-07-20/102466293.html",
}


def fetch_caixin_unrate_history(year_month_list):
    """从财新 (economy.caixin.com) 每月新闻稿抓中国分年龄组失业率。
    NBS 官方 API/data 站 100% 403、TE 公开页只有最新 1 期;
    财新每月20日左右转载 NBS 数据,免费摘要含 16-24/25-29/30-59/总失业率4个值。
    财新 list 页/文章页 urllib 都 403,改走 firecrawl (有 MCP) 抓文章 markdown。
    返回 {date: {cn_youth_16_24, cn_25_29, cn_30_59, cn_total, source_url, headline}}
    """
    result = {}
    for ym in year_month_list:
        if ym not in _CAIXIN_URL_TABLE:
            continue
        article_url = _CAIXIN_URL_TABLE[ym]
        page_text = None
        # 路径 1: firecrawl (有 MCP 服务时优先, 真实浏览器, 不 403)
        try:
            from firecrawl import FirecrawlApp
            app = FirecrawlApp()
            doc = app.scrape_url(article_url, params={"formats": ["markdown"]})
            page_text = (doc or {}).get("markdown", "") or ""
        except Exception:
            page_text = None
        # 路径 2: 直连 _get (部分月份可拉,失败走 firecrawl 缓存)
        if not page_text or "16—24岁" not in page_text:
            try:
                page_text = _get(article_url, timeout=15)
            except Exception:
                pass
        if not page_text:
            continue
        rec = _parse_caixin_unrate(article_url, page_text)
        if rec:
            result[ym] = rec
    return result


def fetch_unemployment_history(prev=None):
    """抓 NBS 分年龄组失业率历史月度序列,写 D:\\osint\\data\\cn_unemployment_history.json。
    prev: 已存在文件内容(增量合并用); None 则全量初始化。
    """
    if prev is None:
        prev = {"series": {}, "metadata": {}}
    # NBS 2023-12 起公布"16-24 不含在校生"新口径,覆盖 2023-12 到当前月
    today = datetime.now(timezone.utc)
    months = []
    y, m = 2023, 12
    while (y, m) <= (today.year, today.month):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    # 1. 抓财新每月报道
    fresh = fetch_caixin_unrate_history(months)
    # 2. WorldBank 年度参考线(15-24, 含学生, ILO 估计)
    wb = fetch_worldbank("CHN", "SL.UEM.1524.ZS")
    # 3. 合并写盘
    series = prev.get("series", {})
    methodology_break = "2023-12"  # 新口径起点
    for ym, rec in fresh.items():
        if "cn_youth_16_24" in rec:
            entry = {
                "date": rec.get("date", ym),
                "value": rec["cn_youth_16_24"],
                "source": rec.get("source", "NBS-via-Caixin"),
                "methodology_break": (rec.get("date", ym) == methodology_break),
            }
            series.setdefault("cn_youth_unrate_16_24", []).append(entry)
        if "cn_25_29" in rec:
            series.setdefault("cn_unrate_25_29", []).append({
                "date": rec.get("date", ym),
                "value": rec["cn_25_29"],
                "source": rec.get("source", "NBS-via-Caixin"),
            })
        if "cn_30_59" in rec:
            series.setdefault("cn_unrate_30_59", []).append({
                "date": rec.get("date", ym),
                "value": rec["cn_30_59"],
                "source": rec.get("source", "NBS-via-Caixin"),
            })
        if "cn_total" in rec:
            series.setdefault("cn_unrate_total", []).append({
                "date": rec.get("date", ym),
                "value": rec["cn_total"],
                "source": rec.get("source", "NBS-via-Caixin"),
            })
    # WB 年度参考线
    if wb and "value" in wb and "error" not in wb:
        series.setdefault("cn_youth_15_24_ilo_annual", []).append({
            "date": wb.get("date", ""),
            "value": wb["value"],
            "source": "WorldBank",
            "note": "15-24 含学生, ILO 估计, 年度, 参考线",
        })
    # 按日期排序
    for k in series:
        series[k] = sorted(series[k], key=lambda x: x.get("date", ""))
    return {
        "updated_at": today.isoformat(),
        "methodology_note": "NBS 城镇调查失业率。2023-08~11 NBS 停发; 2023-12 起 '16-24 不含在校生' 与之前'含在校生'口径不可比, 已标 methodology_break。",
        "source": "NBS-via-Caixin (主) + WorldBank (年度参考)",
        "series": series,
        "stale": len(fresh) == 0,
    }


# 指标清单（按"对年轻失业毕业生视角重要性"排序）
INDICATORS = [
    # 汇率（直接影响海淘/留学/外贸就业）
    {"id": "fx_usd_cny", "label": "美元/人民币", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_frankfurter("USD", "CNY")},
    {"id": "fx_eur_cny", "label": "欧元/人民币", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_frankfurter("EUR", "CNY")},
    {"id": "fx_jpy_cny", "label": "日元/人民币（100日元）", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_frankfurter("JPY", "CNY")},
    # 美国利率（影响全球资本流向、A 股估值）
    {"id": "us_10y", "label": "美国 10 年期国债收益率 (%)", "category": "利率", "fmt": "{:.3f}",
     "fetcher": lambda: fetch_fred("DGS10")},
    {"id": "us_fed_funds", "label": "美联储联邦基金利率 (%)", "category": "利率", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_fred("FEDFUNDS")},
    # 中国通胀（核心视角，NBS 月度数据经 TE 同步）
    {"id": "cn_cpi", "label": "中国 CPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_te_china_indicator("inflation-cpi", "Inflation Rate")},
    # 美国通胀（影响美联储决策，间接影响中国）
    {"id": "us_cpi", "label": "美国 CPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_fred("FPCPITOTLZGUSA")},
    # 中国就业（核心视角指标，NBS 月度数据经 TE 同步）
    {"id": "cn_unrate", "label": "中国城镇调查失业率 (%)", "category": "就业", "fmt": "{:.1f}",
     "fetcher": lambda: fetch_te_china_indicator("unemployment-rate", "Unemployment Rate")},
    # 中国青年失业率（不含在校生16-24岁，NBS月度数据，每月19日左右发布）
    {"id": "cn_youth_unrate", "label": "中国青年失业率 16-24岁 (%)", "category": "就业", "fmt": "{:.1f}",
     "fetcher": lambda: fetch_te_china_indicator("youth-unemployment-rate", "Youth Unemployment Rate")},
    # 中国 GDP（世界银行口径）
    {"id": "cn_gdp_growth", "label": "中国 GDP 同比增速 (%)", "category": "增长", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_worldbank("CHN", "NY.GDP.MKTP.KD.ZG")},
    # 韩国 GDP（东亚对比）
    {"id": "kr_gdp_growth", "label": "韩国 GDP 同比增速 (%)", "category": "增长", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_worldbank("KOR", "NY.GDP.MKTP.KD.ZG")},
    # 美元指数
    {"id": "dxy_proxy", "label": "美元指数代理 (USDEUR)", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_fred("DEXUSEU")},
]


def main():
    # Load previous to keep stale values
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "indicators": [],
    }

    for ind in INDICATORS:
        iid = ind["id"]
        prev_data = prev.get("indicators", {}).get(iid, {})
        fresh = None
        try:
            fresh = ind["fetcher"]()
        except Exception as e:
            fresh = {"error": str(e)[:80]}

        entry = {
            "id": iid,
            "label": ind["label"],
            "category": ind["category"],
            "fmt": ind["fmt"],
        }

        if fresh and "value" in fresh and "error" not in fresh:
            entry.update({
                "value": fresh["value"],
                "date": fresh.get("date", ""),
                "source": fresh.get("source", ""),
                "stale": False,
            })
        else:
            # Stale fallback
            entry.update({
                "stale": True,
                "error": (fresh or {}).get("error", "fetch failed"),
                "value": prev_data.get("value"),
                "date": prev_data.get("date", ""),
                "source": prev_data.get("source", ""),
                "stale_since": prev_data.get("updated_at", ""),
            })

        result["indicators"].append(entry)

    # Convert list to dict for easier lookups
    result["indicators"] = {i["id"]: i for i in result["indicators"]}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[macro] Wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"[macro] {len(INDICATORS)} indicators")
    fresh_count = sum(1 for v in result["indicators"].values() if not v.get("stale"))
    print(f"[macro] {fresh_count} fresh, {len(INDICATORS) - fresh_count} stale")


if __name__ == "__main__":
    import sys
    if "--history" in sys.argv:
        # 只跑历史时间序列抓取
        history_out = ROOT / "data" / "cn_unemployment_history.json"
        prev = {}
        if history_out.exists():
            try:
                prev = json.loads(history_out.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        result = fetch_unemployment_history(prev=prev)
        history_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        total_points = sum(len(v) for v in result["series"].values())
        print(f"[unrate-history] Wrote {history_out} ({history_out.stat().st_size} bytes)")
        print(f"[unrate-history] {len(result['series'])} series, {total_points} total points")
        for sid, points in result["series"].items():
            print(f"  {sid}: {len(points)} points, range {points[0]['date'] if points else '?'} -> {points[-1]['date'] if points else '?'}")
    else:
        main()
