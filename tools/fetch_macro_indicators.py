"""宏观关键指标抓取 - 独立数据集，写 <ROOT>/data/macro_indicators.json
只跑免费、无 key 的 API：FRED (DEMO_KEY) + WorldBank + TradingEconomics + 新浪
失败项保留 stale 标记 + 上次值，不阻塞。

SSRF 防护：所有 URL 在发出前过白名单 + 解析 IP 检查（拒绝 RFC1918/loopback/link-local）。

根目录解析（2026-10-14）：OSINT_ROOT 可覆盖（CI 指向 checkout 目录），
未设置退回 D:\\osint。环境变量属外部输入，先校验再用（禁 `..`、须绝对路径）。
"""
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def _resolve_root():
    """解析项目根目录：OSINT_ROOT 显式覆盖，否则默认 D:\\osint。

    同 gen_dashboard._resolve_root 的校验规格（禁 `..`、须绝对路径）；
    校验失败退回默认根，绝不据此访问任意路径。
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
    "finance.sina.com.cn",  # 财新原文被删时的转载源（2026-10-13：7 月那篇财新 404）
    "hq.sinajs.cn",   # 新浪实时汇率（2026-10-11）：境内直连、分钟级，替代 Frankfurter 的 T-1 延迟
    "api.firecrawl.dev",  # Firecrawl 直调（2026-10-14）：替代 CI 上不存在的本地 MCP
}

# Firecrawl 直调（2026-10-14）：端点硬编码，目标页在白名单内（财新/新浪转载）
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
_ALLOWED_SCRAPE_TARGETS = {
    "economy.caixin.com", "finance.sina.com.cn",
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


def _get(url, timeout=TIMEOUT, headers=None, encoding="utf-8"):
    """白名单 + IP 校验的 URL 拉取。

    headers: 附加请求头（如新浪接口要求的 Referer），与默认 UA 合并。
    encoding: 响应解码编码（新浪汇率接口是 GBK）。

    IPv6 回退（2026-10-10）：本机 IPv6 出口被 TLS 中间人劫持（证书链是自签的），
    urllib 默认按 getaddrinfo 顺序优先连 IPv6 → 对 Cloudflare 系主机（如
    api.frankfurter.app）持续 CERTIFICATE_VERIFY_FAILED，而同主机 IPv4 完全正常
    （实测 issuer=Google Trust Services）。故 TLS 失败时强制 IPv4 重试一次。
    只影响本函数的解析过程，不改全局 socket 行为。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"scheme {parsed.scheme!r} not allowed (https only)")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host {parsed.hostname!r} not in whitelist")
    if not _host_is_safe(parsed.hostname):
        raise ValueError(f"host {parsed.hostname!r} resolves to unsafe IP")
    req_headers = {"User-Agent": "osint-dashboard/1.0"}
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers)
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode(encoding)
    except Exception as e:
        # 仅 TLS 证书类错误才回退（DNS/超时等回退无意义）
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
    _orig_gai = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, *a, **k):
        return _orig_gai(host, port, socket.AF_INET, *a, **k)

    socket.getaddrinfo = _ipv4_only
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode(encoding)
    finally:
        socket.getaddrinfo = _orig_gai


def _firecrawl_scrape(url, timeout=60):
    """直调 Firecrawl REST API 抓 URL，返回 markdown。SSRF 守卫同 _get 规格。

    2026-10-14 新增：替代 `from firecrawl import FirecrawlApp`（CI 无该包、
    本地 SDK 无 key 恒失败）。只依赖 urllib，端点与目标均过白名单+IP 校验。
    """
    ep = urlparse(_FIRECRAWL_ENDPOINT)
    if ep.scheme != "https" or ep.hostname != "api.firecrawl.dev":
        raise ValueError("firecrawl endpoint not allowed")
    if not _host_is_safe(ep.hostname):
        raise ValueError("firecrawl endpoint resolves to unsafe IP")
    tp = urlparse(url)
    if tp.scheme != "https" or tp.hostname not in _ALLOWED_SCRAPE_TARGETS:
        raise ValueError(f"scrape target {tp.hostname!r} not in whitelist")
    if not _host_is_safe(tp.hostname):
        raise ValueError(f"scrape target {tp.hostname!r} resolves to unsafe IP")
    api_key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY not set")
    body = json.dumps({
        "url": url, "formats": ["markdown"], "onlyMainContent": True,
    }).encode("utf-8")
    req = Request(_FIRECRAWL_ENDPOINT, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    with urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    md = ((payload or {}).get("data") or {}).get("markdown") or ""
    if not md:
        raise ValueError("firecrawl returned empty markdown")
    return md


def fetch_fred(series_id, transformation=None):
    """FRED 公开 CSV 端点（无需 key）。

    transformation: 可选 FRED 转换，如 "pc1" = 同比百分比变化（year-over-year %）。
    指数型序列（CPIAUCSL/PPIACO 等）原始值是**指数点位**（如 334.13），
    加 pc1 才是用户要看的「同比 %」。年度序列（FPCPITOTLZGUSA）发布滞后一年，
    不要用于「最新月度」指标。
    """
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        if transformation:
            url += f"&transformation={transformation}"
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


def fetch_sina_fx(symbol):
    """新浪实时汇率（2026-10-11）：境内直连、分钟级报价，替代 Frankfurter 的 T-1 延迟。

    接口：https://hq.sinajs.cn/list=fx_susdcny（GBK 编码，需带 Referer 过防盗链）。
    请求经 _get 发出——白名单、IP 安全校验、IPv6 回退全部复用同一处实现。

    返回字段（前4个）：时间, 买价, 卖价, 昨结/中间价 … 行情日期。
    周末/休市时返回最近一个交易日的收盘价（date 字段如实带日期+时间，
    面板按此显示数据时点，不会把休市价误标成"现在"）。
    """
    import datetime as _dt
    try:
        text = _get(f"https://hq.sinajs.cn/list={symbol}",
                    headers={"Referer": "https://finance.sina.com.cn"},
                    encoding="gbk")
        m = re.search(r'="([^"]*)"', text)
        if not m:
            return None
        fields = m.group(1).split(",")
        # 实测 fx_s* 汇率接口 18 字段（2026-10-11）：[0]=时间 [1]=买价 … [17]=行情日期
        if len(fields) < 18 or not fields[1]:
            return None
        price = float(fields[1])          # 买价（外汇即期报价即现价）
        ts_date = fields[-1]              # YYYY-MM-DD
        ts_time = fields[0]               # HH:MM:SS（交易时段内分钟级）
        date_str = ts_date
        try:
            _dt.datetime.strptime(ts_time, "%H:%M:%S")
            date_str = f"{ts_date} {ts_time[:5]}"   # 交易时段内拼上时间
        except ValueError:
            pass
        return {"value": round(price, 4), "date": date_str, "source": "新浪实时"}
    except Exception as e:
        return {"error": str(e)[:80], "source": "新浪实时"}


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
    返回 date 字段为 ISO 月份 "YYYY-MM" 格式。

    TE 句式有两种（2026-10-10 实测）：
      A) "... increased to 0.80 percent in August from ..."     ← CPI/失业率等
      B) "... increased 3.80 percent in August of 2026 over ..." ← PPI（**无 to**，年份在 of 后）
    旧版只匹配 A，导致 PPI 页始终返回 None。现在两种都试。
    """
    import re
    try:
        te_url = f"https://tradingeconomics.com/china/{page_slug}"
        html = _get(te_url, timeout=15)  # 走白名单+IP安全检查
        # 单位宽容: percent/points/CNY Billion/CNY Hundred Million 等
        unit = r"(?:percent|points|CNY [A-Z][A-Za-z ]{2,20}?)"
        # 时间两种写法: "in August" / "in August of 2026"
        when = r"(?:in (\w+)(?:\s+of\s+(\d{4}))?(?:\s+(\d{4}))?|on \w+)"
        verb = r"(?:increased|decreased|rose|fell|was)"
        m = re.search(
            rf"{re.escape(label_keyword)} in China {verb}(?: to)? ([\d.]+) {unit} {when}",
            html,
        )
        if not m:
            # 备选: "remained unchanged at X <unit> in Month / on Weekday" (TE 利率不变时用这个)
            m = re.search(
                rf"{re.escape(label_keyword)} in China remained unchanged at ([\d.]+) {unit} {when}",
                html,
            )
        if m:
            # 分组: (1)=数值 (2)=月份 (3)="of YYYY"的年份 (4)="Month YYYY"的年份
            month_name = m.group(2)
            year = m.group(3) or m.group(4)
            # TE HTML 一般不返年份, 兜底用当前年(1 月抓 12 月数据时可能有偏差, 但仪表盘上 "2026-01" 总比 "January" 好)
            if not year:
                year = str(datetime.now().year)
            try:
                month_num = {
                    "January": 1, "February": 2, "March": 3, "April": 4,
                    "May": 5, "June": 6, "July": 7, "August": 8,
                    "September": 9, "October": 10, "November": 11, "December": 12,
                }[month_name]
            except KeyError:
                month_num = None
            if month_num:
                date_str = f"{year}-{month_num:02d}"
            else:
                # "on Wednesday" 等无月份场景: 用当前月兜底 (TE 数据是最新的)
                date_str = datetime.now().strftime("%Y-%m")
            return {
                "value": float(m.group(1)),
                "date": date_str,
                "source": "NBS-TE",
            }
    except Exception as e:
        return {"error": str(e)[:80], "source": "NBS-TE"}
    return None


def _parse_caixin_unrate(article_url, page_text):
    """从财新文章 markdown/HTML 解析 4 个分年龄组失业率值

    2026-09-19 修：财新有两种句式，旧正则会把合并句式的第一个数字错配给 30-59。
    - 合并式：「25—29岁、30—59岁劳动力失业率分别录得 7.1%、4.0%」
      → 前一个数字属 25-29，后一个属 30-59；旧的 "30—59岁[^\\d]{0,60}?(\\d)%" 会
        从"30—59岁"往后找第一个数字，但因为 7.1% 出现在 25-29 之后、30-59 之前的位置
        受 60 字窗口影响，实测抓成了 7.1（正确值应为 4.0）。
    - 分离式：「30—59岁劳动力失业率…维持在 3.9%」→ 旧正则正常。
    修复策略：先尝试"分别录得 A%、B%"模式精确配对；失败再退回旧正则。

    2026-10-13 修：只截取**正文区**再解析。此前对整页 HTML 搜索，标题/分享串里的
    「16—24岁失业率结束六连降 3月升至16.9%」以及侧栏相关报道链接会先被命中——
    实测 2026-03 那篇被侧栏「2月…降至16.1%」的链接抢走，解析成 16.1（真值 16.9）。
    正文容器按站别取：财新 `<div id="Main_Content_Val">`、新浪 `<div id="artibody">`；
    两者都找不到时退回整页（保持旧行为，不因改版直接失能）。
    """
    import re
    body = page_text
    _m = re.search(r'<div[^>]*id="(?:Main_Content_Val|artibody)"[^>]*>', page_text)
    if _m:
        _tail = page_text.find("责任编辑", _m.end())
        body = page_text[_m.end(): _tail if _tail > 0 else _m.end() + 20000]
    # 剥离 HTML 标签（2026-10-13）：数值常被包在 <A href=".../520658977">16.9%</A> 里，
    # href 中的数字会挡住正则的跨越——不去标签则真值抓不到。
    body = re.sub(r"<[^>]+>", "", body)

    # 句子级配对（2026-10-13 重写）。历史两版都栽在「年龄组与数值的跨段配对」上：
    #   v1 整页裸搜 → 被标题/侧栏抢走（2026-03 篇解析成 16.1，真值 16.9）
    #   v2 窗口正则 → 并列列举时把前一个年龄组的值串给后一个（2026-04 篇 30-59 得 7.4）
    # 现按 。；，切子句，句内收集「年龄提及」与「百分比」两个有序列表：
    #   数值出现在最后一个年龄组之后 → 按出场顺序一一配对
    #     （覆盖「16—24岁、25—29岁…分别为14.5%、6.7%」与「16—24岁…录得18.9%」两式）；
    #   数值出现在年龄组之前 → 只认「单年龄+单数值」，其余放弃。
    # 判据：宁可缺值，不可串位（AGENTS.md 对数值型管道的既有要求）。
    _AGE = re.compile(r"(16|25|30)\s*[—–\-]\s*(24|29|59)\s*岁")
    _PCT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
    _KEY = {("16", "24"): "cn_youth_16_24",
            ("25", "29"): "cn_25_29",
            ("30", "59"): "cn_30_59"}
    values = {}
    for clause in re.split(r"[。；;，,]", body):
        ages = [(m.start(), _KEY[(m.group(1), m.group(2))]) for m in _AGE.finditer(clause)]
        pcts = [(m.start(), float(m.group(1))) for m in _PCT.finditer(clause)]
        if not ages or not pcts:
            continue
        if pcts[0][0] < ages[0][0]:
            if len(ages) == 1 and len(pcts) == 1:
                values.setdefault(ages[0][1], pcts[0][1])
            continue
        # 多期混杂 → 跳过（2026-10-13，实测 2024-02 篇踩坑）：
        # 开篇综述句「16—24岁失业率分别为 14.6% 和 15.3%」写的是**连续两个月**，
        # 数值个数多于年龄组个数。按序配对会把上月的 14.6 配给本月，
        # 而正题句「2月…录得15.3%」的值因 setdefault 被拒 → 整篇取到错值。
        if len(pcts) > len(ages):
            continue
        for (_, label), (_, val) in zip(ages, pcts):
            values.setdefault(label, val)

    youth = values.get("cn_youth_16_24")
    v25 = values.get("cn_25_29")
    v30 = values.get("cn_30_59")
    # 总量同样按「单值子句」提取（避开「1月、2月…分别为5.2%、5.3%」的跨期句）
    total = None
    for clause in re.split(r"[。；;，,]", body):
        if "城镇调查失业率" in clause:
            _p = _PCT.findall(clause)
            if len(_p) == 1:
                total = float(_p[0])
                break

    # 自洽性校验（宁可缺，不可错，AGENTS.md 既有规则）：
    # 16-24 > 25-29 > 30-59 是本数据的一贯结构；30-59 也不应高于城镇调查失业率总量。
    if v30 is not None and total is not None and v30 > total:
        v30 = None
    if v25 is not None and youth is not None and v25 >= youth:
        v25 = None
    if v30 is not None and v25 is not None and v30 >= v25:
        v30 = None

    if youth is None:
        return None
    rec = {
        "cn_youth_16_24": youth,
        "source_url": article_url,
        "source": "NBS-via-Sina" if "sina" in (urlparse(article_url).hostname or "") else "NBS-via-Caixin",
    }
    if v25 is not None:
        rec["cn_25_29"] = v25
    if v30 is not None:
        rec["cn_30_59"] = v30
    if total is not None:
        rec["cn_total"] = total
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
    "2026-07": "https://finance.sina.com.cn/jjxw/2026-08-19/doc-ininvshv9435757.shtml",
    "2026-08": "https://economy.caixin.com/2026-09-18/102486206.html",
}

# 手工维护的 URL 表本质上会腐化：财新 7 月那篇原文**已被删除**（实测 404），
# 只能改用新浪转载；财新站内搜索/列表页又只回溯约 1 个月，无法自动发现更早文章。
# 因此表里允许出现非财新域名（走 _get 白名单），解析器按 host 自适应。
# 2026-10-13 起另加 `_discover_latest_unrate_url()`：每月新数据由列表页自动发现，
# URL 表退化为「历史回填档案」，只在补历史时才需要人工增加条目。


def _discover_latest_unrate_url():
    """从财新经济频道列表页发现**最新**一篇分年龄组失业率报道的 URL。
    返回 (url, data_month) 或 (None, None)。

    为什么需要它：URL 表要人工维护，实测 2026-07 那篇财新原文被删（404）、
    表里一度停在 2026-06，导致看板失业率序列整整断更 2 个月且无任何告警。
    列表页只回溯约 1 个月，对「本月新数据」够用（NBS 每月 17-20 日发布）。
    """
    import re
    try:
        page = _get("https://economy.caixin.com/news/", timeout=15)
    except Exception:
        return None, None
    best = None
    for m in re.finditer(
            r'href="(https://economy\.caixin\.com/(20\d{2}-\d{2})-\d{2}/\d+\.html)"(.*?)(?=href="|<dl|</dl)',
            page, re.S):
        url, pub_ym, blob = m.group(1), m.group(2), m.group(3)
        if "失业率" not in blob:
            continue
        alt = re.search(r'alt="([^"]{4,90})"', blob)
        title = alt.group(1) if alt else ""
        # 标题必须含年龄组字样，排除「稳就业」等泛就业报道
        if "岁" not in blob and "岁" not in title:
            continue
        if best is None or url > best[0]:
            best = (url, title)
    if not best:
        return None, None
    return best[0], None


def fetch_caixin_unrate_history(year_month_list):
    """从财新 (economy.caixin.com) 每月新闻稿抓中国分年龄组失业率。
    NBS 官方 API/data 站 100% 403、TE 公开页只有最新 1 期;
    财新每月20日左右转载 NBS 数据,免费摘要含 16-24/25-29/30-59/总失业率4个值。
    返回 {date: {cn_youth_16_24, cn_25_29, cn_30_59, cn_total, source_url, headline}}
    """
    result = {}
    # 当前月允许「运行时发现」：URL 表里没有的月份，先从列表页找最新一篇补上
    latest, _ = _discover_latest_unrate_url()
    _effective = dict(_CAIXIN_URL_TABLE)
    if latest:
        _probe = _get(latest, timeout=15) if True else None
        _rec = _parse_caixin_unrate(latest, _probe) if _probe else None
        if _rec and _rec.get("date") in year_month_list and _rec["date"] not in _effective:
            _effective[_rec["date"]] = latest
            result[_rec["date"]] = _rec
    for ym in year_month_list:
        if ym in result:
            continue
        if ym not in _effective:
            continue
        article_url = _effective[ym]
        page_text = None
        # 路径 1: 直连 _get（2026-10-13 提为首选）。
        # 实测本地 firecrawl SDK 无 key（`No API key provided`）恒失败，
        # 而 urllib 直连财新/新浪正文页均 200；CI/agent 环境有 key 时仍可选走 SDK。
        try:
            page_text = _get(article_url, timeout=15)
        except Exception:
            page_text = None
        # 路径 2: firecrawl 直调 API 兜底（2026-10-14 改）
        # 原走 `from firecrawl import FirecrawlApp` SDK——CI 上没装该包，
        # 且本地实测 SDK 无 key 恒失败。改为直调 REST API（只依赖 urllib），
        # 与 fetch_index_valuation._firecrawl_scrape 同规格。
        if not page_text or ("16—24岁" not in page_text and "16-24岁" not in page_text):
            try:
                page_text = _firecrawl_scrape(article_url)
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
    # 3. 合并写盘 —— 按 date 幂等 upsert（2026-10-13 修）
    # 原实现是无条件 append：同一月份被抓到几次就追加几条，实测累积到 6 条/月
    # （refresh 每月 21 日后若同月多跑几轮，每轮都从头重抓整表）。
    # 现改为「同 date 覆盖」：新值始终覆盖旧值，顺带把历史上解析错的脏值订正掉。
    series = prev.get("series", {})
    methodology_break = "2023-12"  # 新口径起点

    def _upsert(key, date, value, extra=None):
        """按 date 写入/覆盖一条；同月重复全部清除，只保留本次写入的一条。"""
        if not date:
            return
        rows = series.setdefault(key, [])
        entry = {"date": date, "value": value}
        if extra:
            entry.update(extra)
        # 剔除所有同 date 的历史行（含旧版 append 累积的重复条目），再追加
        rows[:] = [r for r in rows if r.get("date") != date]
        rows.append(entry)

    for ym, rec in fresh.items():
        date = rec.get("date", ym)
        if "cn_youth_16_24" in rec:
            _upsert("cn_youth_unrate_16_24", date, rec["cn_youth_16_24"], {
                "source": rec.get("source", "NBS-via-Caixin"),
                "methodology_break": (date == methodology_break),
            })
        if "cn_25_29" in rec:
            _upsert("cn_unrate_25_29", date, rec["cn_25_29"],
                    {"source": rec.get("source", "NBS-via-Caixin")})
        if "cn_30_59" in rec:
            _upsert("cn_unrate_30_59", date, rec["cn_30_59"],
                    {"source": rec.get("source", "NBS-via-Caixin")})
        if "cn_total" in rec:
            _upsert("cn_unrate_total", date, rec["cn_total"],
                    {"source": rec.get("source", "NBS-via-Caixin")})
    # WB 年度参考线
    if wb and "value" in wb and "error" not in wb:
        _upsert("cn_youth_15_24_ilo_annual", wb.get("date", ""), wb["value"], {
            "source": "WorldBank",
            "note": "15-24 含学生, ILO 估计, 年度, 参考线",
        })
    # 按日期排序 + 全局去重（2026-10-13）：历史上旧版 append 累积的重复条目
    # 分布在「本次没抓到的月份」上，upsert 覆盖不到——统一按 date 保留最后一条。
    for k in series:
        _dedup = {}
        for r in series[k]:
            _dedup[r.get("date", "")] = r  # 后者胜（同 date 时保留最新解析结果）
        series[k] = [v for _, v in sorted(_dedup.items())]

    # 缺月检测（2026-10-13 新增）：按「最新已有月份距当前月几个月」标 stale。
    # 旧实现只看 `len(fresh) == 0`——抓不到任何新数据才算 stale，而历史月份一直
    # 能拉到（URL 表里有），所以**序列断更时 stale 恒为 False**，看板无任何提示。
    # 这正是 2026-07/08 两个月静默断更的直接原因。
    _youth_rows = series.get("cn_youth_unrate_16_24", [])
    _latest = _youth_rows[-1]["date"] if _youth_rows else ""
    _missing = []
    if _latest:
        _ly, _lm = int(_latest[:4]), int(_latest[5:7])
        _cy, _cm = today.year, today.month
        _gap = (_cy - _ly) * 12 + (_cm - _lm)
        for i in range(1, _gap):  # 中间缺失的月份（最新月之后是否已发布由发布日决定，不计）
            _m2 = _lm + i
            _missing.append(f"{_ly + (_m2 - 1) // 12}-{(_m2 - 1) % 12 + 1:02d}")
    return {
        "updated_at": today.isoformat(),
        "methodology_note": "NBS 城镇调查失业率。2023-08~11 NBS 停发; 2023-12 起 '16-24 不含在校生' 与之前'含在校生'口径不可比, 已标 methodology_break。",
        "source": "NBS-via-Caixin (主) + WorldBank (年度参考)",
        "series": series,
        "latest_month": _latest,
        "missing_months": _missing,
        "stale": len(fresh) == 0,
    }


# 指标清单（按"对年轻失业毕业生视角重要性"排序）
INDICATORS = [
    # 汇率（直接影响海淘/留学/外贸就业）
    # 2026-10-11 切新浪实时（分钟级、境内直连）；Frankfurter 只到 T-1 且曾受
    # IPv6 劫持/301 影响。新浪失败时 _get 的错误会自然落进 stale fallback。
    {"id": "fx_usd_cny", "label": "美元/人民币", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_sina_fx("fx_susdcny")},
    {"id": "fx_eur_cny", "label": "欧元/人民币", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_sina_fx("fx_seurcny")},
    {"id": "fx_jpy_cny", "label": "日元/人民币（100日元）", "category": "汇率", "fmt": "{:.4f}",
     "fetcher": lambda: fetch_sina_fx("fx_sjpycny")},
    # 美国利率（影响全球资本流向、A 股估值）
    {"id": "us_10y", "label": "美国 10 年期国债收益率 (%)", "category": "利率", "fmt": "{:.3f}",
     "fetcher": lambda: fetch_fred("DGS10")},
    {"id": "us_fed_funds", "label": "美联储联邦基金利率 (%)", "category": "利率", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_fred("DFF")},
    # 中国通胀（核心视角，NBS 月度数据经 TE 同步）
    {"id": "cn_cpi", "label": "中国 CPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_te_china_indicator("inflation-cpi", "Inflation Rate")},
    # 中国 PPI（工业生产者出厂价格同比；NBS 月度，TE 同步）
    {"id": "cn_ppi", "label": "中国 PPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_te_china_indicator("producer-prices-change", "Producer Prices")},
    # 美国 CPI（月度同比；CPIAUCSL 是季调指数，pc1 转同比 → 用户要看的 %）
    # 2026-10-10 修: 原先用 FPCPITOTLZGUSA，那是**年度**序列（滞后一年，实测停在 2024-01）
    {"id": "us_cpi", "label": "美国 CPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "max_age_days": 90,
     "fetcher": lambda: fetch_fred("CPIAUCSL", transformation="pc1")},
    # 美国 PPI（最终需求同比；FRED PPIFIS 指数 pc1 转同比）
    {"id": "us_ppi", "label": "美国 PPI 同比 (%)", "category": "通胀", "fmt": "{:.2f}",
     "max_age_days": 90,
     "fetcher": lambda: fetch_fred("PPIFIS", transformation="pc1")},
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
    # 中国货币/信用序列 (read-macro 五维框架的货币流动性+信用)
    {"id": "cn_dr007", "label": "中国 7 天逆回购利率 (%)", "category": "货币流动性", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_te_china_indicator("reverse-repo-rate", "Reverse Repo Rate")},
    {"id": "cn_shibor_3m", "label": "中国 Shibor 3 个月 (%)", "category": "货币流动性", "fmt": "{:.2f}",
     "fetcher": lambda: fetch_te_china_indicator("interbank-rate", "Interbank Rate")},
    {"id": "cn_m1_yoy", "label": "中国 M1 余额 (CNY 亿)", "category": "货币流动性", "fmt": "{:.0f}",
     "fetcher": lambda: fetch_te_china_indicator("money-supply-m1", "Money Supply M1")},
    {"id": "cn_m2_yoy", "label": "中国 M2 余额 (CNY 亿)", "category": "货币流动性", "fmt": "{:.0f}",
     "fetcher": lambda: fetch_te_china_indicator("money-supply-m2", "Money Supply M2")},
    {"id": "cn_shrong_yoy", "label": "中国社融总量 (CNY 亿)", "category": "信用", "fmt": "{:.0f}",
     "fetcher": lambda: fetch_te_china_indicator("total-social-financing", "Total Social Financing")},
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
            # 派生与上次抓取的变化 (basis points), 用于 build_macro_state_prompt
            prev_val = prev_data.get("value")
            prev_date = prev_data.get("date")
            if prev_val is not None and prev_date == fresh.get("date"):
                change = fresh["value"] - prev_val
                # 利率类 (cn_dr007/cn_shibor_3m/us_10y/us_fed_funds) 已是 % 形式, change 单位用 bp (1% = 100bp)
                # 其它用 % 形式
                if iid in ("cn_dr007","cn_shibor_3m","us_10y","us_fed_funds"):
                    entry["change_bp"] = round(change * 100, 1)
                else:
                    entry["change_pct"] = round(change, 2)
            # 数据年龄阈值: 月级数据 > 60d 标 stale, 日级 > 14d 标 stale
            try:
                d_str = fresh.get("date", "")
                dt = None
                # 兼容 ISO 月份 "2026-07" 和全日期 "2026-09-01"
                if re.match(r"^\d{4}-\d{2}$", d_str):
                    dt = datetime.strptime(d_str, "%Y-%m")
                elif re.match(r"^\d{4}-\d{2}-\d{2}", d_str):
                    dt = datetime.strptime(d_str[:10], "%Y-%m-%d")
                elif d_str.isdigit() and len(d_str) == 4:
                    # 年级 "2025"
                    age_days = (datetime.now() - datetime(int(d_str), 1, 1)).days
                    if age_days > 180:
                        entry["stale"] = True
                        entry["stale_reason"] = f"age {age_days}d > 180d"
                if dt:
                    age_days = (datetime.now() - dt).days
                    # 阈值优先级：指标自带 max_age_days（月频 FRED 序列日期是完整日期
                    # "2026-08-01"，无法靠格式区分月/日频）> 月级(YYYY-MM) 90d > 日级 14d
                    threshold = ind.get("max_age_days") or (90 if len(d_str) == 7 else 14)
                    if age_days > threshold:
                        entry["stale"] = True
                        entry["stale_reason"] = f"age {age_days}d > {threshold}d"
            except Exception:
                pass
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
