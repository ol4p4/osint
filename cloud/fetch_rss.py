import feedparser
import hashlib
import json
import time
import urllib.request
import socket
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
import re
import sys


class RSSFetcher:
    def __init__(self, sources_config, keyword_weights):
        self.sources = sources_config
        self.keyword_weights = keyword_weights
    
    def fetch_all(self, max_age_hours=168):
        all_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        for source in self.sources:
            try:
                items = self._fetch_source(source, cutoff_time)
                all_items.extend(items)
                print(f"[RSS] {source['name']}: {len(items)} items")
            except Exception as e:
                print(f"[RSS ERROR] {source['name']}: {e}")
        
        return all_items
    
    def _fetch_source(self, source, cutoff_time):
        url = source["url"]
        # Fetch with timeout using urllib
        try:
            socket.setdefaulttimeout(15)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(512000)  # Max 500KB
            feed = feedparser.parse(raw)
        except Exception as e:
            print(f"  Fetch failed: {e}")
            return []
        
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
        text_lower = text.lower()
        hits = []
        score = 0
        for kw, weight in self.keyword_weights.items():
            if kw.lower() in text_lower:
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
    
    with open(output_file, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    print(f"Total: {len(items)} items -> {output_file}")
