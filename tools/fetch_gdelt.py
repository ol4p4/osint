# -*- coding: utf-8 -*-
r"""fetch_gdelt.py - 同类方案调研 P1-5：GDELT 国际侧结构化事件补源
目的：33 个 scope:ci 外国源依赖 CI 境外环境，挂了就空窗；GDELT DOC API 免费、无认证、
100+ 语言，可从本地直接补国际侧事件流（中文源仍靠金十/新浪/RSSHub——GDELT 中文覆盖差）。
产出 title-only 英文条目 append 到今日 jsonl，自然流入 translate_local 翻译管线。

SSRF 防护与 verify_hypotheses.py 同款：仅 https + 域名白名单 + 解析结果不得指向私有地址。
用法：python tools/fetch_gdelt.py [--dry]   （--dry 只打印不落盘；失败静默退出码 0，不阻塞 refresh）
"""
import argparse
import hashlib
import json
import re
import socket
import sys
import time
import ipaddress
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

BASE = Path(r"D:\osint\data")

# SSRF 白名单：只允许 GDELT 官方 API（独立于 verify_hypotheses 的白名单）
ALLOWED_HOSTS = {"api.gdeltproject.org"}

# 查询组：GDELT DOC API 2.0（query 语法 = 空格分隔 AND 词）
QUERY_GROUPS = [
    ("china economy OR \"chinese economy\"", "macro"),
    ("china trade OR tariffs OR \"export controls\"", "trade"),
    ("china geopolitics OR taiwan OR \"south china sea\"", "geopolitics"),
]
MAX_RECORDS = 75
LANG = "sourcelang:eng"      # 只要英文源（翻译管线兜底中文）
MIN_TITLE_LEN = 30           # 过短标题是噪音
SLEEP_BETWEEN = 6            # 组间隔：GDELT 限速严（实测 2s 间隔即吃 429）
RETRY_BACKOFF = 12           # 429/坏响应退避后重试一次
STATE_FILE = Path(r"D:\osint\data\.gdelt_last_run")   # 成功时间戳，供 refresh 节流


def _safe_fetch_json(url, timeout=15):
    """白名单 + 地址解析校验的 https GET"""
    from urllib.parse import urlparse
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
        return json.loads(resp.read().decode("utf-8", "replace"))


def _norm_seen(ts):
    """GDELT seendate '20260905T063000Z' → '2026-09-05 06:30'"""
    m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", str(ts or ""))
    if not m:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"


def fetch_gdelt(max_records=MAX_RECORDS):
    """跑三组查询 → 去重 → 映射成 intel 条目。返回 (items, errors)"""
    items = []
    errors = []
    seen_urls = set()
    def _get(u):
        # 429/非 JSON 响应退避后重试一次（GDELT 限速严格）
        for attempt in (1, 2):
            try:
                return _safe_fetch_json(u)
            except json.JSONDecodeError:
                last = "non-json response"
            except Exception as e:
                last = f"{type(e).__name__} {str(e)[:80]}"
            if attempt == 1:
                time.sleep(RETRY_BACKOFF)
        raise RuntimeError(last)

    for q, category in QUERY_GROUPS:
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + quote(q + " " + LANG)
               + f"&mode=artlist&maxrecords={max_records}&format=json&sort=datedesc&timespan=24h")
        try:
            data = _get(url)
        except Exception as e:
            errors.append(f"query[{q[:30]}]: {str(e)[:80]}")
            continue
        arts = data.get("articles", [])
        got = 0
        for a in arts:
            u = a.get("url", "")
            title = (a.get("title") or "").strip()
            if not u or u in seen_urls or len(title) < MIN_TITLE_LEN:
                continue
            seen_urls.add(u)
            domain = a.get("domain", "gdelt")
            eid = hashlib.sha256(f"GDELT:{u}:{title}".encode("utf-8")).hexdigest()[:16]
            items.append({
                "id": eid,
                "source_name": "GDELT:" + domain,
                "title": title,
                "url": u,
                "summary": "",
                "published_at": _norm_seen(a.get("seendate")),
                "fetched_at": datetime.now().isoformat(),
                "category": category,
                "language": a.get("language", "eng"),
                "base_score": 0.3,          # 无关键词评分，给保底分，AI 研判层兜底
                "source_country": a.get("sourcecountry", ""),
            })
            got += 1
        print(f"[GDELT] {q[:40]!r} → {got} 条")
        time.sleep(SLEEP_BETWEEN)
    return items, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    ap.add_argument("--force", action="store_true", help="忽略节流强制抓取")
    ap.add_argument("--throttle-hours", type=float, default=3.0,
                    help="距上次成功不足 N 小时则跳过（refresh 每小时调用时的限速阀）")
    args = ap.parse_args()

    # 节流：成功后写时间戳；hourly refresh 靠它避免连环撞 GDELT 限速
    if not args.dry and not args.force and STATE_FILE.exists():
        try:
            age_h = (time.time() - float(STATE_FILE.read_text(encoding="utf-8").strip())) / 3600
            if age_h < args.throttle_hours:
                print(f"[GDELT] skip (上次成功 {age_h:.1f}h 前 < {args.throttle_hours}h)")
                return
        except Exception:
            pass

    items, errors = fetch_gdelt()
    if errors:
        print(f"[GDELT] 部分查询失败(不阻塞): {errors}")
    if not items:
        print("[GDELT] 0 条（网络不可达或无结果）——失败不写状态文件，下轮重试")
        return

    if args.dry:
        for it in items[:5]:
            print(" ·", it["published_at"], "|", it["source_name"], "|", it["title"][:60])
        print(f"[GDELT] dry 模式共 {len(items)} 条，未落盘")
        return

    out = BASE / ("intel_" + datetime.now().strftime("%Y%m%d") + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    # 幂等：读已有 id，跨轮去重
    existing = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    existing.add(json.loads(line).get("id"))
                except Exception:
                    pass
    fresh = [it for it in items if it["id"] not in existing]
    with out.open("a", encoding="utf-8") as fh:
        for it in fresh:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    STATE_FILE.write_text(str(time.time()), encoding="utf-8")
    print(f"[GDELT] append {len(fresh)}/{len(items)} 条 -> {out}")


if __name__ == "__main__":
    main()
