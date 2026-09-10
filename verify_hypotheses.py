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
    entry = inds[hit_id]
    val = entry.get("value")
    if val is None:
        return None
    return {"value": float(val), "date": entry.get("date", ""), "source": "macro_snapshot:" + hit_id}

def fetch_indicator_value(indicator_name):
    """Try multiple sources to fetch indicator value
    优先级：本地宏观快照 → FRED → WorldBank → Frankfurter（汇率类）"""
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

def evaluate_hypothesis(hyp, evidence_from_intel=None):
    """Evaluate a single hypothesis based on indicator values
    P0-2: 阈值真正比较数值；refute 命中降置信、support 命中升置信；
    无数据源的指标标 no_source（留给周循环 AI 裁判），自由文本阈值标 needs_ai"""
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
        if not (thr_refute is not None or thr_support is not None):
            ind["verify_status"] = "needs_ai"  # 自由文本阈值，走周循环 AI 裁判

    # Adjust confidence based on evidence
    # 幂等闸门（2026-09-10 修复）：信号状态未变化时不再重复应用增量——
    # 静态指标（WorldBank 年度值等）反复运行曾会每次叠减/叠加，置信度单向漂移到夹紧边界。
    # 指标增量按"信号状态变化"触发（sN_rM 快照对比）；intel 关键词微调按日闸（每日至多一次）。
    prev_stats = hyp.get("verify_stats") or {}
    cur_sig = "s%d_r%d" % (support_hits, refute_hits)
    old_confidence = float(hyp.get("confidence", 0.5) or 0.5)
    new_confidence = old_confidence
    applied = False

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

    hyp["confidence"] = round(new_confidence, 2)
    hyp["verify_stats"] = {"checked_at": today_str, "indicators_checked": checked,
                           "support_hits": support_hits, "refute_hits": refute_hits,
                           "signal_state": cur_sig,
                           "confidence_applied": applied,
                           "intel_adjusted_at": (today_str if (evidence_from_intel and not (refute_hits or support_hits))
                                                 else prev_stats.get("intel_adjusted_at"))}
    return hyp, updates

def load_intel_for_matching():
    """Load recent intelligence for hypothesis matching"""
    files = sorted(INTEL_DIR.glob("intel_*.jsonl"), reverse=True)[:3]  # Last 3 days

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
    total_updates = 0
    for hyp in hyps:
        if hyp.get("level") != "small":
            continue  # Only verify small hypotheses with indicators

        hyp, updates = evaluate_hypothesis(hyp, intel_items)
        total_updates += len(updates)

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
            print(f"\n{hyp['id']}: {hyp['title']}")
            for u in updates:
                print(u)

    # Save updated hypotheses
    HYP_FILE.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save history
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 完成 ===")
    print(f"指标更新: {total_updates} 项")
    print(f"历史记录: {len(history)} 个指标")

if __name__ == "__main__":
    main()
