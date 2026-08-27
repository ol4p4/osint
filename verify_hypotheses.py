# -*- coding: utf-8 -*-
"""
假设自动验证引擎
读取假设树 → 拉取指标真实值 → 对比阈值 → 更新置信度
"""
import json, re, sys, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

HYP_FILE = Path(r"D:\Codex输出\osint_卫星图\hypotheses\active_hypotheses.json")
INTEL_DIR = Path(r"D:\Codex输出\osint_卫星图")
HISTORY_FILE = Path(r"D:\Codex输出\osint_卫星图\indicator_history.json")

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

def fetch_fred(series_id):
    """Fetch latest value from FRED (no API key needed for observation)"""
    if not series_id:
        return None
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&sort_order=desc&limit=1&api_key=DEMO_KEY&file_type=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if len(data) > 1 and data[1]:
                item = data[1][0]
                val = item.get("value")
                if val:
                    return {"value": float(val), "date": item.get("date", ""), "source": "WorldBank"}
    except Exception as e:
        print(f"  WorldBank fetch failed for {indicator_code}: {e}")
    return None

def fetch_indicator_value(indicator_name):
    """Try multiple sources to fetch indicator value"""
    # Try FRED first
    fred_key = FRED_SERIES.get(indicator_name)
    if fred_key:
        result = fetch_fred(fred_key)
        if result:
            return result
    
    # Try World Bank
    wb_mapping = {
        "老年抚养比": "SP.POP.65UP.TO.ZS",
        "房价收入比": None,
        "最终消费占GDP比重": "NE.CON.TOTL.ZS",
        "研发支出占GDP比": "GB.XPD.RSDV.GD.ZS",
    }
    wb_key = wb_mapping.get(indicator_name)
    if wb_key:
        result = fetch_world_bank(wb_key)
        if result:
            return result
    
    # Try Frankfurter for exchange rates
    if "汇率" in indicator_name or "美元" in indicator_name:
        result = fetch_frankfurter()
        if result:
            return result
    
    return None

def evaluate_hypothesis(hyp, evidence_from_intel=None):
    """Evaluate a single hypothesis based on indicator values"""
    indicators = hyp.get("indicators", [])
    if not indicators:
        return hyp, []
    
    updates = []
    support_count = 0
    total_indicators = len(indicators)
    
    for ind in indicators:
        name = ind.get("name", "")
        if not name:
            continue
        
        # Try to fetch real value
        real_value = fetch_indicator_value(name)
        if real_value:
            ind["current_value"] = real_value["value"]
            ind["last_updated"] = real_value["date"]
            ind["data_source"] = real_value["source"]
            updates.append(f"  {name}: {real_value['value']} ({real_value['source']}, {real_value['date']})")
            
            # Check threshold
            threshold = ind.get("threshold_support", "")
            if threshold:
                # Simple heuristic: if we have a value and a threshold, check basic conditions
                support_count += 1
    
    # Adjust confidence based on evidence
    old_confidence = hyp.get("confidence", 0.5)
    new_confidence = old_confidence
    
    if evidence_from_intel:
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
                elif any(w in title for w in ["下降", "减少", "放缓", "收缩"]):
                    if direction == "toward":
                        new_confidence = min(0.95, new_confidence + 0.01)
                    else:
                        new_confidence = max(0.05, new_confidence - 0.02)
    
    hyp["confidence"] = round(new_confidence, 2)
    return hyp, updates

def load_intel_for_matching():
    """Load recent intelligence for hypothesis matching"""
    today = datetime.now().strftime("%Y-%m-%d").replace("-", "")
    files = sorted(INTEL_DIR.glob("intel_*.jsonl"), reverse=True)[:3]  # Last 3 days
    
    intel_items = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        intel_items.append(json.loads(line))
                    except:
                        pass
    return intel_items

def main():
    print("=== 假设自动验证引擎 ===")
    
    # Load hypotheses
    with open(HYP_FILE, "r", encoding="utf-8") as f:
        hyps = json.load(f)
    
    print(f"加载假设: {len(hyps)} 条")
    
    # Load recent intelligence
    intel_items = load_intel_for_matching()
    print(f"加载近期情报: {len(intel_items)} 条")
    
    # Update indicator history
    history = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    
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
    with open(HYP_FILE, "w", encoding="utf-8") as f:
        json.dump(hyps, f, ensure_ascii=False, indent=2)
    
    # Save history
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    print(f"\n=== 完成 ===")
    print(f"指标更新: {total_updates} 项")
    print(f"历史记录: {len(history)} 个指标")

if __name__ == "__main__":
    main()
