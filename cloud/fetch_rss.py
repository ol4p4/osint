#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端情报官 - RSS源采集器
使用feedparser解析RSS，输出标准化JSONL
"""

import feedparser
import hashlib
import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
import re


class RSSFetcher:
    def __init__(self, sources_config: List[Dict], keyword_weights: Dict[str, float]):
        self.sources = sources_config
        self.keyword_weights = keyword_weights
    
    def fetch_all(self, max_age_hours: int = 168) -> List[Dict[str, Any]]:
        all_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        for source in self.sources:
            try:
                items = self._fetch_source(source, cutoff_time)
                all_items.extend(items)
                print(f"[RSS] {source['name']}: {len(items)} 条")
            except Exception as e:
                print(f"[RSS ERROR] {source['name']}: {e}")
        
        return all_items
    
    def _fetch_source(self, source: Dict, cutoff_time: datetime) -> List[Dict[str, Any]]:
        feed = feedparser.parse(source["url"])
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
                "content": content[:5000],
                "summary": content[:200],
                "published_at": published.isoformat() if published else datetime.now(timezone.utc).isoformat(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "base_score": round(min(base_score, 1.0), 3),
                "keywords_hit": keywords_hit,
                "entities": self._extract_entities(content),
                "lang": "zh"
            }
            items.append(item)
        
        return items
    
    def _parse_entry_time(self, entry) -> Optional[datetime]:
        for key in ["published_parsed", "updated_parsed", "created_parsed"]:
            if key in entry and entry[key]:
                try:
                    return datetime(*entry[key][:6], tzinfo=timezone.utc)
                except:
                    pass
        return None
    
    def _extract_content(self, entry) -> str:
        for key in ["content", "summary", "description"]:
            if key in entry:
                val = entry[key]
                if isinstance(val, list) and val:
                    val = val[0].get("value", "") if isinstance(val[0], dict) else val[0]
                if isinstance(val, str) and len(val) > 50:
                    clean = re.sub(r"<[^>]+>", "", val)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    return clean
        return ""
    
    def _make_id(self, source_name: str, url: str, title: str) -> str:
        raw = f"{source_name}|{url}|{title}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    
    def _calc_keyword_score(self, text: str) -> tuple:
        text_lower = text.lower()
        hits = []
        total_weight = 0.0
        for kw, weight in self.keyword_weights.items():
            if kw.lower() in text_lower:
                hits.append(kw)
                total_weight += weight
        score = min(total_weight / 6.0, 1.0)
        return hits, score
    
    def _extract_entities(self, text: str) -> List[str]:
        entities = []
        patterns = [
            r"\b[A-Z]{2,}\b",
            r"(国务院|发改委|工信部|人社部|教育部|财政部|央行|证监会|银保监会|海关总署|统计局|外交部|国资委)",
            r"(习近平|李强|李云泽|潘功胜|吴清|何立峰|丁薛祥|韩正|蔡奇|李希)",
            r"(中石油|中石化|中海油|国家电网|南方电网|五大发电|华能|大唐|华电|国电投|国家能源集团)",
        ]
        for pat in patterns:
            matches = re.findall(pat, text)
            entities.extend(matches)
        return list(set(entities))[:10]


def load_config(config_path: str) -> Dict:
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    import sys
    config = load_config(sys.argv[1] if len(sys.argv) > 1 else "sources.yaml")
    fetcher = RSSFetcher(config.get("rss_sources", []), config.get("keyword_weights", {}))
    items = fetcher.fetch_all()
    
    for item in items:
        print(json.dumps(item, ensure_ascii=False))
