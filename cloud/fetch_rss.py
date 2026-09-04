import feedparser
import hashlib
import json
import os
import time
import urllib.request
import socket
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
import re
import sys


# RSSHub 兜底链: 本地容器优先(CI 内自建), 失败依次退公共镜像。
# 本地 Docker/RSSHub 不在时 (日常本机常态), 7/9 路由可由镜像兜底;
# 金十/新浪已改为官方直连 API (sources.yaml direct 字段), 不经过任何 RSSHub。
RSSHUB_BASES = ["http://localhost:1200", "https://hub.slarker.me", "https://rsshub.rssforever.com"]
_env_bases = os.environ.get("RSSHUB_BASES", "").strip()
if _env_bases:
    RSSHUB_BASES = [b.strip().rstrip("/") for b in _env_bases.split(",") if b.strip()]
# 命中这些 netloc 的源视为 RSSHub 路由, 走兜底链
RSSHUB_NETLOCS = {"localhost:1200", "127.0.0.1:1200", "rsshub.app"}


def _safe_rss_fetch(url, timeout=15, max_bytes=512000):
    """RSS 源为配置文件中的任意站点：仅允许 http/https + 解析结果不得指向私有/环回/保留地址"""
    import ipaddress
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("scheme not allowed: " + str(parsed.scheme))
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for info in socket.getaddrinfo(host, port):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback:
            # 环回放行：CI 内自起的 RSSHub 容器(localhost:1200)是可信的本地转换服务
            continue
        if ip.is_private or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("host resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes)


class RSSFetcher:
    def __init__(self, sources_config, keyword_weights):
        self.sources = sources_config
        self.keyword_weights = keyword_weights
        self._kw_patterns = None  # 关键词正则缓存(词边界), 首次打分时编译
    
    def fetch_all(self, max_age_hours=168, max_workers=8):
        """2026-09-04 并发化: 串行 50 源 x 15s 超时上限 ≈ 最坏 12.5min(实测 ~13min),
        是 CI job 耗时主因。改 8 线程并发, 每源独立超时/独立兜底, 源间无共享状态。
        预期 CI 采集 ~2min, 本地 17 源 ~10s。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        all_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        def _one(source):
            try:
                return source, self._fetch_source(source, cutoff_time), None
            except Exception as e:
                return source, [], e

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_one, s) for s in self.sources]
            for fut in as_completed(futures):
                source, items, err = fut.result()
                if err:
                    print(f"[RSS ERROR] {source['name']}: {err}")
                else:
                    all_items.extend(items)
                    print(f"[RSS] {source['name']}: {len(items)} items")

        return all_items
    
    def _fetch_source(self, source, cutoff_time):
        url = source["url"]
        # JSON 直连源 (sources.yaml direct 字段): 官方 API, 不依赖 RSSHub
        direct = source.get("direct")
        if direct:
            return self._fetch_direct(source, direct, cutoff_time)
        # RSSHub 路由源: 本地容器 -> 公共镜像 逐级兜底, 全部失败才放弃
        try:
            parsed = urlparse(url)
        except Exception:
            parsed = None
        if parsed and parsed.netloc in RSSHUB_NETLOCS:
            route = parsed.path
            for base in RSSHUB_BASES:
                try:
                    items = self._fetch_rss_url(source, base + route, cutoff_time)
                    if items:
                        if base != RSSHUB_BASES[0]:
                            print(f"  [RSSHub fallback] {source['name']} via {base}")
                        return items
                except Exception as e:
                    print(f"  [RSSHub] {base}{route} failed: {e}")
            print(f"  [RSSHub] all bases failed for {source['name']}")
            return []
        try:
            return self._fetch_rss_url(source, url, cutoff_time)
        except Exception as e:
            print(f"  Fetch failed: {e}")
            return []

    def _fetch_rss_url(self, source, url, cutoff_time):
        socket.setdefaulttimeout(15)
        raw = _safe_rss_fetch(url)
        feed = feedparser.parse(raw)

        items = []
        for entry in feed.entries:
            published = self._parse_entry_time(entry)
            if published and published < cutoff_time:
                continue
            
            content = self._extract_content(entry)
            if not content or len(content) < 50:
                continue
            
            uid = self._make_id(source["name"], entry.get("link", ""), entry.get("title", ""))
            keywords_hit, kw_score = self._calc_keyword_score(entry.get("title", "") + " " + content)
            base_score = source.get("weight", 1.0) * kw_score
            
            item = {
                "id": uid,
                "source": "rss",
                "source_name": source["name"],
                "category": source.get("category", "rss"),
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "content_preview": content[:2000],
                "published_at": published.isoformat() if published else datetime.now(timezone.utc).isoformat(),
                "keywords_hit": keywords_hit,
                "base_score": round(base_score, 3),
                "language": self._detect_lang(entry.get("title", "") + " " + content),
                "entities": [],
            }
            items.append(item)
        
        return items

    # ---- JSON 直连源 (2026-09-04, 摆脱 RSSHub 依赖) ----

    def _fetch_direct(self, source, kind, cutoff_time):
        try:
            if kind == "jin10_flash":
                items = self._direct_jin10(source, cutoff_time)
            elif kind == "sina_zhibo":
                items = self._direct_sina_zhibo(source, cutoff_time)
            elif kind == "sina_roll":
                items = self._direct_sina_roll(source, cutoff_time)
            else:
                print(f"  [direct] unknown kind: {kind}")
                items = []
            print(f"  [direct:{kind}] {len(items)} items")
            return items
        except Exception as e:
            print(f"  [direct:{kind}] Fetch failed: {e}")
            return []

    def _direct_item(self, source, title, content, link, published_utc):
        """直连条目 -> 标准情报 dict (与 RSS 路径同结构/同过滤/同打分)"""
        content = re.sub(r"<[^>]+>", " ", content or "")
        content = re.sub(r"\s+", " ", content).strip()
        if not content or len(content) < 50:
            return None
        uid = self._make_id(source["name"], link, title)
        keywords_hit, kw_score = self._calc_keyword_score(title + " " + content)
        return {
            "id": uid,
            "source": "rss",
            "source_name": source["name"],
            "category": source.get("category", "rss"),
            "title": (title or content[:60]).strip(),
            "url": link,
            "content_preview": content[:2000],
            "published_at": published_utc.isoformat(),
            "keywords_hit": keywords_hit,
            "base_score": round(source.get("weight", 1.0) * kw_score, 3),
            "language": self._detect_lang(title + " " + content),
            "entities": [],
        }

    def _direct_jin10(self, source, cutoff_time):
        """金十快讯官方接口: flash_newest.js -> var newest = [...] (北京时间字符串)"""
        raw = _safe_rss_fetch("https://www.jin10.com/flash_newest.js", timeout=15)
        m = re.search(r"\[.*\]", raw.decode("utf-8", "replace"), re.S)
        data = json.loads(m.group(0)) if m else []
        tz8 = timezone(timedelta(hours=8))
        out = []
        for it in data[:80]:
            d = it.get("data") or {}
            content = str(d.get("content") or "").strip()
            title = str(d.get("title") or "").strip()
            if not content and not title:
                continue
            try:
                dt = datetime.strptime(it.get("time", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz8).astimezone(timezone.utc)
            except Exception:
                continue
            if dt < cutoff_time:
                continue
            item = self._direct_item(source, title or content[:60],
                                     title + " " + content if title else content,
                                     f"https://www.jin10.com/flash/{it.get('id', '')}", dt)
            if item:
                out.append(item)
        return out

    def _direct_sina_zhibo(self, source, cutoff_time):
        """新浪 7x24 直播官方接口: zhibo_id=152 (create_time 为北京时间字符串)"""
        raw = _safe_rss_fetch(
            "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=100", timeout=15)
        lst = json.loads(raw.decode("utf-8", "replace"))["result"]["data"]["feed"]["list"]
        tz8 = timezone(timedelta(hours=8))
        out = []
        for it in lst:
            text = str(it.get("rich_text") or "").strip()
            if not text:
                continue
            ct = it.get("create_time")
            try:
                if isinstance(ct, (int, float)):
                    dt = datetime.fromtimestamp(int(ct), tz=timezone.utc)
                else:
                    dt = datetime.strptime(str(ct), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz8).astimezone(timezone.utc)
            except Exception:
                continue
            if dt < cutoff_time:
                continue
            item = self._direct_item(source, text[:60], text,
                                     f"https://finance.sina.com.cn/7x24/?id={it.get('id', '')}", dt)
            if item:
                out.append(item)
        return out

    def _direct_sina_roll(self, source, cutoff_time):
        """新浪滚动新闻官方接口: pageid=153&lid=2509 (ctime 为 epoch 秒)"""
        raw = _safe_rss_fetch(
            "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1", timeout=15)
        lst = json.loads(raw.decode("utf-8", "replace"))["result"]["data"]
        out = []
        for it in lst:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            try:
                dt = datetime.fromtimestamp(int(it.get("ctime")), tz=timezone.utc)
            except Exception:
                continue
            if dt < cutoff_time:
                continue
            item = self._direct_item(source, title, str(it.get("intro") or title),
                                     str(it.get("url") or ""), dt)
            if item:
                out.append(item)
        return out

    def _parse_entry_time(self, entry):
        for field in ["published_parsed", "updated_parsed"]:
            t = getattr(entry, field, None)
            if t:
                try:
                    return datetime(*t[:6], tzinfo=timezone.utc)
                except:
                    pass
        for field in ["published", "updated"]:
            s = getattr(entry, field, None)
            if s:
                try:
                    from email.utils import parsedate_to_datetime
                    return parsedate_to_datetime(s).astimezone(timezone.utc)
                except:
                    pass
        return None
    
    def _extract_content(self, entry):
        content = ""
        if hasattr(entry, "summary"):
            content = entry.summary
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", content)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        return content
    
    def _make_id(self, source_name, url, title):
        raw = f"{source_name}:{url}:{title}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]
    
    def _calc_keyword_score(self, text):
        """2026-09-04 修复: 纯 ASCII 关键词改词边界匹配。
        旧实现裸子串, "AI" 命中 said/chairman/maintain 等一切含 'ai' 的词 →
        所有英文新闻白拿关键词分, 打分被系统性污染。中文关键词无词边界概念, 保持子串。
        正则预编译一次 (原先每条目对 153 个词重复做子串扫描)。
        """
        if self._kw_patterns is None:
            self._kw_patterns = []
            for kw, weight in self.keyword_weights.items():
                k = kw.lower()
                if k.isascii() and k[0].isalnum() and k[-1].isalnum():
                    pat = re.compile(r"\b" + re.escape(k) + r"\b")
                else:
                    pat = re.compile(re.escape(k))
                self._kw_patterns.append((kw, weight, pat))
        text_lower = text.lower()
        hits = []
        score = 0
        for kw, weight, pat in self._kw_patterns:
            if pat.search(text_lower):
                hits.append(kw)
                score += weight
        return hits, min(score, 1.0)
    
    def _detect_lang(self, text):
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        total_chars = len(text)
        if total_chars == 0:
            return "unknown"
        return "zh" if chinese_chars / total_chars > 0.1 else "en"


if __name__ == "__main__":
    import yaml
    
    sources_file = sys.argv[1] if len(sys.argv) > 1 else "sources.yaml"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "rss_output.jsonl"
    
    with open(sources_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    all_sources = []
    for key in ["rss_sources", "east_asia_sources"]:
        all_sources.extend(config.get(key, []))
    
    weights = config.get("keyword_weights", {})
    
    fetcher = RSSFetcher(all_sources, weights)
    items = fetcher.fetch_all(max_age_hours=72)

    Path(output_file).write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8")
    
    print(f"Total: {len(items)} items -> {output_file}")
