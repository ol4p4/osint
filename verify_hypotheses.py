# -*- coding: utf-8 -*-
"""
假设自动验证引擎
读取假设树 → 拉取指标真实值 → 对比阈值 → 更新置信度

2026-09-05 P0-2 修复（同类方案调研）：
- 原实现的阈值判断是空壳：threshold 字符串非空就 support_count += 1，从不比较数值
- 现把 '>2.5%' '<200bp' '>=2' 等简式阈值解析为 (op, 数值, 单位) 真正比较；
  自由文本叙述阈值标记 needs_ai，由周循环的 AI 裁判兜底
- 指标值优先读本地 data/macro_indicators.json（refresh.py 每小时已抓，免外发请求），
  再退 FRED/WorldBank/Frankfurter（域名白名单不变）
- deadline 回填见 tools/fill_deadline.py（空/远期 deadline 导致验证从不触发的另一半修复）
"""
import json, re, sys, socket, ipaddress, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

# SSRF 防护：仅 https + 域名白名单 + 解析结果不得指向私有/环回/保留地址
ALLOWED_HOSTS = {
    "api.stlouisfed.org", "api.frankfurter.app", "api.gold-api.com", "api.worldbank.org",
}

def _safe_fetch_bytes(url, timeout=10):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only https allowed")
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ValueError("host not allowed: " + host)
    for info in socket.getaddrinfo(host, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("host resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def _fetch_json(url, timeout=10):
    return json.loads(_safe_fetch_bytes(url, timeout))

HYP_FILE = Path(r"D:\osint\data\hypotheses\active_hypotheses.json")
INTEL_DIR = Path(r"D:\osint\data")
HISTORY_FILE = Path(r"D:\osint\data\indicator_history.json")
MACRO_FILE = Path(r"D:\osint\data\macro_indicators.json")
FACTS_FILE = Path(r"D:\osint\data\indicator_facts.json")

# FRED series mapping for known indicators
FRED_SERIES = {
    "青年失业率": "LRUNTTTTCN156S",
    "老年抚养比": "SPPOP65UPTOT14-CN",
    "养老金替代率": None,  # No FRED series
    "CPI": "CPALCN01CAM661N",
    "PPI": None,
    "M1": "MABMM101CN189S",
    "M2": "MABMM201CN189S",
    "LPR": None,
    "国内原油产量": "CHNRCOILPROD",
    "全球新增可再生能源装机": None,
    "中国对美出口占比": None,
    "稀土出口配额": None,
    "碳酸锂现货价": None,
}

# NBS indicators
NBS_INDICATORS = {
    "工业增加值": "A010101",
    "固定资产投资": "A020101",
    "CPI月度": "A090101",
}

# World Bank indicators（匹配放宽为子串：假设树指标名常带括号后缀）
WB_MAPPING = {
    "老年抚养比": "SP.POP.65UP.TO.ZS",
    "房价收入比": None,
    "最终消费占GDP比重": "NE.CON.TOTL.ZS",
    "研发支出占GDP比": "GB.XPD.RSDV.GD.ZS",
}

# 假设树中文指标名 → macro_indicators.json 指标 id（按列表顺序做包含匹配，长别名在前；
# 值为 None 表示显式排除该别名，防止误配）
MACRO_ALIASES = [
    ("美国10年期国债收益率", "us_10y"), ("美债收益率", "us_10y"),
    ("联邦基金利率", "us_fed_funds"), ("美联储利率", "us_fed_funds"),
    ("青年失业率", "cn_youth_unrate"),
    ("社会融资存量", "cn_shrong_yoy"), ("社融存量", "cn_shrong_yoy"), ("社会融资", "cn_shrong_yoy"), ("社融", "cn_shrong_yoy"),
    ("美元兑人民币", "fx_usd_cny"), ("人民币汇率", "fx_usd_cny"),
    ("SHIBOR", "cn_shibor_3m"), ("shibor", "cn_shibor_3m"), ("同业拆借利率", "cn_shibor_3m"),
    ("DR007", "cn_dr007"), ("dr007", "cn_dr007"),
    ("美国CPI", "us_cpi"), ("美国通胀", "us_cpi"),
    ("全球GDP", None),
    ("失业率", "cn_unrate"),
    ("CPI", "cn_cpi"), ("通胀率", "cn_cpi"), ("居民消费价格", "cn_cpi"),
    ("M1", "cn_m1_yoy"), ("M2", "cn_m2_yoy"),
    ("GDP增速", "cn_gdp_growth"),
    ("汇率", "fx_usd_cny"),
]

# ---------- P0-2: 阈值解析与数值比较 ----------

THRESH_RE = re.compile(r"^(>=|<=|==|=|>|<)?\s*(-?\d+(?:\.\d+)?)\s*(%|个百分点|bp|基点|点|亿|万|美元|元)?\s*$")

def parse_threshold(expr):
    """'>2.5%' '<200bp' '>=2' '0' → (op, number, unit)；自由文本叙述返回 None"""
    if not isinstance(expr, str):
        return None
    m = THRESH_RE.match(expr.strip())
    if not m:
        return None
    op = m.group(1) or "="
    if op == "==":
        op = "="
    return op, float(m.group(2)), m.group(3) or ""

def threshold_satisfied(value, thr):
    """数值比较。量纲约定：指标值按'百分比数字'存储（FRED/macro_indicators 均如此），
    bp/基点阈值除以 100 折算成百分点；其余单位直接比数值。"""
    op, num, unit = thr
    if unit in ("bp", "基点"):
        num /= 100.0
    if op == ">":
        return value > num
    if op == "<":
        return value < num
    if op == ">=":
        return value >= num
    if op == "<=":
        return value <= num
    return abs(value - num) < 1e-9

def fetch_fred(series_id):
    """Fetch latest value from FRED (no API key needed for observation)"""
    if not series_id:
        return None
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&sort_order=desc&limit=1&api_key=DEMO_KEY&file_type=json"
        data = _fetch_json(url)
        obs = data.get("observations", [])
        if obs:
            val = obs[0].get("value")
            if val and val != ".":
                return {"value": float(val), "date": obs[0].get("date", ""), "source": "FRED"}
    except Exception as e:
        print(f"  FRED fetch failed for {series_id}: {e}")
    return None

def fetch_frankfurter(from_currency="USD", to_currency="CNY"):
    """Fetch exchange rate from Frankfurter API"""
    try:
        url = f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}"
        data = _fetch_json(url)
        rate = data.get("rates", {}).get(to_currency)
        if rate:
            return {"value": rate, "date": data.get("date", ""), "source": "Frankfurter"}
    except Exception as e:
        print(f"  Frankfurter fetch failed: {e}")
    return None

def fetch_gold():
    """Fetch gold price"""
    try:
        url = "https://api.gold-api.com/price/XAU"
        data = _fetch_json(url)
        price = data.get("price")
        if price:
            return {"value": float(price), "date": datetime.now().strftime("%Y-%m-%d"), "source": "GoldAPI"}
    except:
        pass
    return None

def fetch_world_bank(indicator_code, country="CN"):
    """Fetch from World Bank API"""
    try:
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator_code}?format=json&per_page=1&mrv=1"
        data = _fetch_json(url)
        if len(data) > 1 and data[1]:
            item = data[1][0]
            val = item.get("value")
            if val:
                return {"value": float(val), "date": item.get("date", ""), "source": "WorldBank"}
    except Exception as e:
        print(f"  WorldBank fetch failed for {indicator_code}: {e}")
    return None

# 语义护栏（2026-10-10 加）：MACRO_ALIASES 是子串匹配，会把「派生/比较量」或
# 「他国口径」的指标错配到单一原始值上。实测放开层级后 8 个命中里 6 个错：
#   「青年失业率跨国差异」← 中国 18.9（单值冒充跨国差异）
#   「核心PCE通胀率」    ← 中国 CPI 0.8（口径完全不同）
#   「AI资本开支增速与GDP增速偏离度」← GDP 增速 4.96（单值冒充偏离度）
# 按 AGENTS.md「数值型抓取必须内置自洽校验，宁可缺不可错」——语义不符一律返回
# None，让指标落 no_source 交周循环 AI 裁判，而不是把错值当"已验证"喂给下游。
MACRO_DERIVED_MARKERS = ("差异", "差距", "收敛", "偏离", "比值", "比率", "占比",
                         "排名", "对比", "增速差", "相关性", "贡献度")
# 速率型：需要同比/环比变化量，水平型序列（WB/宏观快照存的都是水平值）答不了
MACRO_RATE_MARKERS = ("年均增速", "增速", "增长率", "变化率", "同比", "环比",
                      "年增幅", "涨跌幅")
MACRO_REGION_RULES = (
    (("美国", "美联储", "美债", "PCE"), ("us_",)),
    (("日本",), ("japan_", "jp_")),
    (("韩国",), ("kr_",)),
    (("欧元区", "欧洲", "欧元"), ("eur_", "eu_")),
)

def _macro_semantic_ok(indicator_name, hit_id):
    """指标名与命中的宏观序列是否语义相符（派生量 / 地域口径双重校验）"""
    for mk in MACRO_DERIVED_MARKERS:
        if mk in indicator_name:
            return False       # 派生量：单一序列答不了，需计算或 AI 裁判
    for markers, prefixes in MACRO_REGION_RULES:
        if any(m in indicator_name for m in markers):
            if not any(hit_id.startswith(p) for p in prefixes):
                return False   # 地域口径不符（如"美国…"填了 cn_ 序列）
    return True

def lookup_macro(indicator_name):
    """从本地宏观快照取值（refresh.py 每小时产出，含 17 个指标）"""
    if not MACRO_FILE.exists():
        return None
    try:
        data = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    inds = data.get("indicators", {})
    hit_id = None
    for alias, mid in MACRO_ALIASES:
        if alias and alias in indicator_name:
            hit_id = mid
            break
    if not hit_id or hit_id not in inds:
        return None
    if not _macro_semantic_ok(indicator_name, hit_id):
        return None
    entry = inds[hit_id]
    val = entry.get("value")
    if val is None:
        return None
    return {"value": float(val), "date": entry.get("date", ""), "source": "macro_snapshot:" + hit_id}

def lookup_facts(indicator_name):
    """检索核实层（2026-10-10）：读 data/indicator_facts.json，按指标名精确匹配。

    定位：`fetch_indicator_value` 的其他分支都是**自动抓取**（映射表里没有就返回
    None → 面板显示"未知"）。但"未知"≠"查不到"——SIPRI 军费、DSCA 对台军售这类
    公开数据只是管道没接上。本层存**经检索核实、带来源 URL + 原文引证**的值，
    由 tools/indicator_facts.py 维护。

    只做精确名匹配（不做子串），避免重蹈 MACRO_ALIASES 语义错配的覆辙。
    """
    if not FACTS_FILE.exists():
        return None
    try:
        facts = (json.loads(FACTS_FILE.read_text(encoding="utf-8")) or {}).get("facts", {})
    except Exception:
        return None
    e = facts.get(indicator_name)
    if not e or e.get("value") is None:
        return None
    val = e["value"]
    unit = e.get("unit") or ""
    # 口径说明必须随值一起带出（2026-10-10）：检索值常与阈值**基数年份不同**
    # （如 SIPRI 给 2024→2025 增速，而阈值要"较 2026 年"），AI 判定/面板
    # 若看不到 note 会把不同基数的数字直接比，得出错误结论。
    return {"value": val,
            "date": e.get("as_of", ""),
            "source": f"检索核实:{e.get('source_name') or '未标'}"
                      + (f"（{unit}）" if unit else ""),
            "source_url": e.get("source_url", ""),
            "quote": e.get("quote", ""),
            "note": e.get("note", "")}

def fetch_indicator_value(indicator_name):
    """Try multiple sources to fetch indicator value
    优先级：检索核实层 → 本地宏观快照 → FRED → WorldBank → Frankfurter（汇率类）

    派生量前置拦截（2026-10-10）：指标名若是「差异/占比/偏离/收敛」等派生量，
    任何单一原始序列都答不了它——必须由计算或 AI 裁判给出。实测放开层级后
    「老年抚养比年均增速」被 WorldBank 的抚养比**水平值** 14.91 填上、
    「AI资本开支增速与GDP增速偏离度」被 GDP 增速填上，都是量纲/语义错配。
    """
    # ⓪ 检索核实层（2026-10-10）：显式维护、带来源 URL + 原文引证的值。
    # **必须先于派生量护栏**——护栏挡的是"自动匹配填错值"，而事实库是人工/agent
    # 核实过的，它答得了派生量（如"三国军费开支同比增长率"可由 SIPRI 三国增速算出）。
    fact = lookup_facts(indicator_name)
    if fact:
        return fact

    for mk in MACRO_DERIVED_MARKERS:
        if mk in indicator_name:
            return None

    # ① 本地宏观快照（免外发请求）
    local = lookup_macro(indicator_name)
    if local:
        return local

    # ② FRED
    fred_key = FRED_SERIES.get(indicator_name)
    if fred_key:
        result = fetch_fred(fred_key)
        if result:
            return result

    # ③ World Bank（子串匹配）
    # 速率型护栏（2026-10-10）：WB_MAPPING 全是**水平值**序列（抚养比/占比），
    # 答不了"增速/年均/变化率"。「老年抚养比年均增速」曾被抚养比水平值 14.91 填上，
    # 量纲错配。宏观快照分支不受此限（cn_gdp_growth 本就是速率序列）。
    if not any(rk in indicator_name for rk in MACRO_RATE_MARKERS):
        for key, wb_code in WB_MAPPING.items():
            if wb_code and (key == indicator_name or key in indicator_name):
                result = fetch_world_bank(wb_code)
                if result:
                    return result

    # ④ 汇率类走 Frankfurter
    if "汇率" in indicator_name or "美元" in indicator_name:
        result = fetch_frankfurter()
        if result:
            return result

    return None

# ---------- 情报计数降级读数（2026-10-10） ----------
# 定位：给**没有免费 API** 的指标（SIPRI/各国国防部/TrendForce 等）补一个客观读数，
# 让面板不再只有"无数据源"三个字。**纯展示用途**：
#   - 不进 threshold_eval、不参与 support/refute 计数、不改 confidence；
#   - 只回答"近 N 天语料里有多少条在谈这件事"，不回答"达标没有"。
# 为什么不做成自动判定（AGENTS.md 教训）：关键词计数衡量的是**话题热度**，不是
# "这条情报是否支持该假设"——曾把「三战在5年内爆发」从 0.05 推到 0.94。所以此处
# 严格限定为"提及计数"，并在 UI 上明示"非阈值判定"。
INTEL_TOPIC_TABLE = [
    ("台海军演", ["军演", "演习", "实弹", "巡弋", "绕台"]),
    ("对台军售", ["军售", "军购", "对台", "台湾关系法"]),
    ("国防开支", ["军费", "国防预算", "国防开支", "防务开支"]),
    ("青年就业", ["青年失业", "毕业生就业", "青年就业", "失业率"]),
    ("房地产", ["房价", "房地产", "商品房", "楼市", "房贷"]),
    ("养老金社保", ["养老金", "养老保险", "社保", "退休金"]),
    ("能源供应", ["原油", "石油", "天然气", "LNG", "油价"]),
    ("芯片半导体", ["芯片", "半导体", "晶圆", "光刻"]),
    ("AI算力", ["算力", "大模型", "AI芯片", "数据中心"]),
    ("贸易关税", ["关税", "贸易战", "出口管制", "制裁"]),
]

def _intel_text(item):
    return " ".join(str(item.get(k) or "") for k in
                    ("title", "cn_title", "summary", "cn_summary"))

def count_intel_for_indicator(indicator_name, hyp_title, intel_items, window_days=3):
    """按受控主题词表给无源指标补「提及计数」读数（纯展示）"""
    if not intel_items:
        return None
    hay = (hyp_title or "") + " " + (indicator_name or "")
    best = None
    for topic, kws in INTEL_TOPIC_TABLE:
        if any(kw in hay for kw in kws):
            cnt = sum(1 for it in intel_items
                      if any(kw in _intel_text(it) for kw in kws))
            if best is None or cnt > best[1]:
                best = (topic, cnt)
    if best is None:
        return None
    return {"topic": best[0], "count": best[1], "window_days": window_days,
            "note": "情报提及计数，非阈值判定"}

def evaluate_hypothesis(hyp, evidence_from_intel=None, write_confidence=True):
    """Evaluate a single hypothesis based on indicator values
    P0-2: 阈值真正比较数值；refute 命中降置信、support 命中升置信；
    无数据源的指标标 no_source（留给周循环 AI 裁判），自由文本阈值标 needs_ai。

    write_confidence=False（2026-10-10 加）：只做读数与判定标注，不写 confidence。
    用于非 small 层——medium/major/mega 的置信度归 ACH 后验与周循环裁判所有
    （AGENTS.md「置信度只能由判定层写」）；若这里也按指标信号加减，会与 ACH
    贝叶斯后验反复互相覆盖。"""
    indicators = hyp.get("indicators", [])
    if not indicators:
        return hyp, []

    updates = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    support_hits = refute_hits = checked = 0

    for ind in indicators:
        name = ind.get("name", "")
        if not name:
            continue

        # Try to fetch real value
        real_value = fetch_indicator_value(name)
        if not real_value:
            ind["verify_status"] = "no_source"
            # 清掉旧抓取读数（2026-10-10）：否则护栏拦下的错值会以"陈旧 current_value"
            # 形式留在面板上继续冒充已验证。只清带 data_source 标记的（抓取产物），
            # 保留假设生成时 AI 写的基线（source 为空，非抓取）。
            if ind.get("data_source"):
                ind.pop("current_value", None)
                ind.pop("last_updated", None)
                ind.pop("data_source", None)
                ind.pop("threshold_eval", None)
            # 降级读数（2026-10-10）：无免费 API 的指标所属假设补「情报提及计数」，
            # 纯展示，不进阈值判定、不改 confidence（理由见 count_intel_for_indicator 注释）。
            # 挂在**假设级**（下方统一算）——按指标挂会让同域 20 个指标重复打印同一计数。
            continue
        ind["current_value"] = real_value["value"]
        ind["last_updated"] = real_value["date"]
        ind["data_source"] = real_value["source"]
        updates.append(f"  {name}: {real_value['value']} ({real_value['source']}, {real_value['date']})")

        # P0-2 修复：解析阈值并真正比较（原实现只判字符串非空就计数）
        signal = "none"
        thr_refute = parse_threshold(ind.get("threshold_refute", ""))
        thr_support = parse_threshold(ind.get("threshold_support", ""))
        if thr_refute is not None or thr_support is not None:
            checked += 1
        if thr_refute is not None and threshold_satisfied(real_value["value"], thr_refute):
            refute_hits += 1
            signal = "refute"
            updates.append(f"    ↳ 命中证伪阈值 {ind.get('threshold_refute')!r} (当前值 {real_value['value']})")
        elif thr_support is not None and threshold_satisfied(real_value["value"], thr_support):
            support_hits += 1
            signal = "support"
            updates.append(f"    ↳ 命中支持阈值 {ind.get('threshold_support')!r} (当前值 {real_value['value']})")
        ind["threshold_eval"] = {
            "checked_at": today_str,
            "value": real_value["value"],
            "refute_expr": ind.get("threshold_refute", ""),
            "support_expr": ind.get("threshold_support", ""),
            "refute_parsed": thr_refute is not None,
            "support_parsed": thr_support is not None,
            "signal": signal,
        }
        # verify_status 三态（2026-10-10 明确化，供面板区分显示）：
        #   checked_met  = 取到值且命中支持/证伪阈值（判定发生了）
        #   checked_unmet= 取到值、阈值可解析，但未命中（真正的"未达阈值"）
        #   needs_ai     = 取到值但阈值是叙述式文本，数值比较不适用 → 交周循环 AI 裁判
        if signal in ("support", "refute"):
            ind["verify_status"] = "checked_met"
        elif thr_refute is not None or thr_support is not None:
            ind["verify_status"] = "checked_unmet"
        else:
            ind["verify_status"] = "needs_ai"  # 自由文本阈值，走周循环 AI 裁判

    # 假设级降级读数（2026-10-10）：本假设若有 no_source 指标，补一个话题提及计数。
    # 挂在假设级而非指标级——同域 20 个指标会重复打印同一计数（噪声）。
    if any(ind.get("verify_status") == "no_source" for ind in indicators):
        ic = count_intel_for_indicator(hyp.get("title", ""), hyp.get("title", ""),
                                       evidence_from_intel)
        if ic:
            hyp["intel_count"] = ic
            updates.append(f"  无源指标 {sum(1 for i in indicators if i.get('verify_status')=='no_source')} 个；"
                           f"近{ic['window_days']}天情报提及「{ic['topic']}」{ic['count']} 条（非阈值判定）")
    else:
        hyp.pop("intel_count", None)

    # Adjust confidence based on evidence
    # 幂等闸门（2026-09-10 修复）：信号状态未变化时不再重复应用增量——
    # 静态指标（WorldBank 年度值等）反复运行曾会每次叠减/叠加，置信度单向漂移到夹紧边界。
    # 指标增量按"信号状态变化"触发（sN_rM 快照对比）；intel 关键词微调按日闸（每日至多一次）。
    prev_stats = hyp.get("verify_stats") or {}
    cur_sig = "s%d_r%d" % (support_hits, refute_hits)
    old_confidence = float(hyp.get("confidence", 0.5) or 0.5)
    new_confidence = old_confidence
    applied = False

    if write_confidence:
        if (refute_hits or support_hits) and prev_stats.get("signal_state") != cur_sig:
            # P0-2: 指标阈值信号驱动置信度（证伪信号权重 > 支持信号）
            if refute_hits:
                new_confidence = max(0.05, new_confidence - 0.05 * refute_hits)
            if support_hits:
                new_confidence = min(0.95, new_confidence + 0.03 * support_hits)
            applied = True

        # 情报关键词微调：仅在无指标信号时生效，且每日至多应用一次
        if (not (refute_hits or support_hits) and evidence_from_intel
                and prev_stats.get("intel_adjusted_at") != today_str):
            for intel in evidence_from_intel:
                # Simple keyword matching to determine support/contradict
                title = intel.get("cn_title", "") + " " + intel.get("cn_summary", "")
                hyp_title = hyp.get("title", "") + " " + hyp.get("rationale", "")

                # Check if intel keywords match hypothesis
                hyp_keywords = set(re.findall(r'[\u4e00-\u9fff]+', hyp_title))
                intel_keywords = set(re.findall(r'[\u4e00-\u9fff]+', title))
                overlap = hyp_keywords & intel_keywords

                if len(overlap) >= 2:
                    # Likely related - check direction
                    direction = hyp.get("direction", "toward")
                    if any(w in title for w in ["增长", "上升", "加速", "扩大"]):
                        new_confidence = min(0.95, new_confidence + 0.02)
                        applied = True
                    elif any(w in title for w in ["下降", "减少", "放缓", "收缩"]):
                        if direction == "toward":
                            new_confidence = min(0.95, new_confidence + 0.01)
                        else:
                            new_confidence = max(0.05, new_confidence - 0.02)
                        applied = True

    if write_confidence:
        hyp["confidence"] = round(new_confidence, 2)
    hyp["verify_stats"] = {"checked_at": today_str, "indicators_checked": checked,
                           "support_hits": support_hits, "refute_hits": refute_hits,
                           "signal_state": cur_sig,
                           "confidence_applied": applied,
                           "readonly": (not write_confidence),
                           "intel_adjusted_at": (today_str if (write_confidence and evidence_from_intel
                                                              and not (refute_hits or support_hits))
                                                 else prev_stats.get("intel_adjusted_at"))}
    return hyp, updates

def load_intel_for_matching():
    """Load recent intelligence for hypothesis matching"""
    # 排除 raw/final 且按日期排序（2026-09-30 修）：字符串排序会把
    # intel_final_* 排到 intel_2* 之前，挤占 3 天窗口的名额。
    files = [f for f in sorted(INTEL_DIR.glob("intel_2*.jsonl"), reverse=True)
             if "raw" not in f.name and "final" not in f.name][:3]  # Last 3 days

    intel_items = []
    for f in files:
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    intel_items.append(json.loads(line))
                except Exception:
                    pass
    return intel_items

def main():
    print("=== 假设自动验证引擎 ===")

    # CI 环境 data/ 不入库，假设树不存在 → 优雅跳过（原实现会直接抛异常被 CI || 吞掉）
    if not HYP_FILE.exists():
        print(f"hypotheses file not found: {HYP_FILE} — skip")
        return

    # Load hypotheses
    hyps = json.loads(HYP_FILE.read_text(encoding="utf-8"))

    print(f"加载假设: {len(hyps)} 条")

    # Load recent intelligence
    intel_items = load_intel_for_matching()
    print(f"加载近期情报: {len(intel_items)} 条")

    # Update indicator history
    history = {}
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Evaluate each hypothesis
    # 2026-10-10 放开层级：此前只跑 level=="small"，导致 medium/major/mega 的
    # 83 个指标从头到尾没人读（99 个指标「从未验证」的根因之一）。
    # 现在所有带指标的层级都做「取数 + 阈值判定 + 标注 verify_status」，
    # 但只有 small 层写 confidence——其余层的置信度归 ACH 后验与周循环裁判
    # （AGENTS.md「置信度只能由判定层写，挂载层不许碰」）。
    total_updates = 0
    layer_counter = {}
    for hyp in hyps:
        if not (hyp.get("indicators") or []):
            continue  # 无指标节点无需取数
        lv = hyp.get("level") or "none"
        write_conf = (lv == "small")
        hyp, updates = evaluate_hypothesis(hyp, intel_items, write_confidence=write_conf)
        total_updates += len(updates)
        layer_counter[lv] = layer_counter.get(lv, 0) + 1

        # Update history
        for ind in hyp.get("indicators", []):
            name = ind.get("name", "")
            if name and ind.get("current_value") is not None:
                if name not in history:
                    history[name] = []
                # Check if today already recorded
                existing_dates = [h["date"] for h in history[name]]
                if today_str not in existing_dates:
                    history[name].append({
                        "date": today_str,
                        "value": ind["current_value"],
                        "source": ind.get("data_source", "")
                    })

        if updates:
            print(f"\n{hyp['id']} [{lv}]: {hyp['title']}")
            for u in updates:
                print(u)

    # Save updated hypotheses
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save history
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 完成 ===")
    print(f"指标更新: {total_updates} 项")
    print(f"历史记录: {len(history)} 个指标")
    if layer_counter:
        print("按层级处理: " + ", ".join(f"{k}={v}" for k, v in sorted(layer_counter.items())))
    # 覆盖率自检：多少指标真正拿到了数值（取不到值的会标 no_source，面板显示"无数据源"）
    total_ind = sum(len(h.get("indicators") or []) for h in hyps)
    with_val = sum(1 for h in hyps for ind in (h.get("indicators") or [])
                   if ind.get("current_value") is not None)
    print(f"指标取数覆盖: {with_val}/{total_ind}"
          f"（其余按 verify_status 标注 no_source/needs_ai，交周循环 AI 裁判）")

if __name__ == "__main__":
    main()
