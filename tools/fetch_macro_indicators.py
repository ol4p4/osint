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
    main()
