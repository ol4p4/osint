#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端情报官 - 列表页采集器（政府门户、统计数据、智库研报）
使用requests + lxml解析列表页，抓取详情页内容
"""

import requests
from lxml import html
import hashlib
import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
import re
import yaml


class ListPageFetcher:
    def __init__(self, sources_config: List[Dict], keyword_weights: Dict[str, float]):
        self.sources = sources_config
        self.keyword_weights = keyword_weights
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self.session.timeout = 15
    
    def fetch_all(self, max_age_hours: int = 168) -> List[Dict[str, Any]]:
        all_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        for source in self.sources:
            try:
                items = self._fetch_source(source, cutoff_time)
                all_items.extend(items)
                print(f"[LIST] {source['name']}: {len(items)} 条")
            except Exception as e:
                print(f"[LIST ERROR] {source['name']}: {e}")
        
        return all_items
    
    def _fetch_source(self, source: Dict, cutoff_time: datetime) -> List[Dict[str, Any]]:
        resp = self.session.get(source["url"])
        resp.encoding = resp.apparent_encoding or "utf-8"
        doc = html.fromstring(resp.text)
        
        list_sel = source.get("list_selector", "a")
        links = doc.cssselect(list_sel)[:source.get("max_items", 20)]
        
        items = []
        for link in links:
            try:
                href = link.get("href", "")
                if not href:
                    continue
                detail_url = urljoin(source["url"], href)
                
                detail_item = self._fetch_detail(detail_url, source, cutoff_time)
                if detail_item:
                    items.append(detail_item)
                    time.sleep(0.5)
            except Exception as e:
                print(f"  [DETAIL ERROR] {href}: {e}")
                continue
        
        return items
    
    def _fetch_detail(self, url: str, source: Dict, cutoff_time: datetime) -> Optional[Dict[str, Any]]:
        try:
            resp = self.session.get(url)
            resp.encoding = resp.apparent_encoding or "utf-8"
            doc = html.fromstring(resp.text)
            
            title_sel = source.get("title_selector", "h1")
            title_elem = doc.cssselect(title_sel)
            title = title_elem[0].text_content().strip() if title_elem else ""
            
            content_sel = source.get("content_selector", "body")
            content_elem = doc.cssselect(content_sel)
            content = content_elem[0].text_content().strip() if content_elem else ""
            content = re.sub(r"\s+", " ", content)
            
            if len(content) < 100:
                return None
            
            date_sel = source.get("date_selector", "")
            published = None
            if date_sel:
                date_elem = doc.cssselect(date_sel)
                if date_elem:
                    published = self._parse_chinese_date(date_elem[0].text_content().strip())
            
            if published and published < cutoff_time:
                return None
            
            keywords_hit, kw_score = self._calc_keyword_score(title + " " + content)
            base_score = source.get("weight", 1.0) * kw_score
            
            uid = self._make_id(source["name"], url, title)
            
            return {
                "id": uid,
                "source": "list_page",
                "source_name": source["name"],
                "category": source.get("category", "gov"),
                "title": title,
                "url": url,
                "content": content[:5000],
                "summary": content[:200],
                "published_at": published.isoformat() if published else datetime.now(timezone.utc).isoformat(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "base_score": round(min(base_score, 1.0), 3),
                "keywords_hit": keywords_hit,
                "entities": self._extract_entities(content),
                "lang": "zh"
            }
        except Exception as e:
            print(f"  [DETAIL PARSE ERROR] {url}: {e}")
            return None
    
    def _parse_chinese_date(self, text: str) -> Optional[datetime]:
        patterns = [
            r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?",
            r"(\d{2})[-/](\d{1,2})[-/](\d{1,2})",
            r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                try:
                    groups = m.groups()
                    if len(groups) >= 3:
                        year = int(groups[0])
                        month = int(groups[1])
                        day = int(groups[2])
                        hour = int(groups[3]) if len(groups) > 3 else 0
                        minute = int(groups[4]) if len(groups) > 4 else 0
                        if year < 100:
                            year += 2000
                        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                except:
                    pass
        return None
    
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


class APIFetcher:
    def __init__(self, sources_config: List[Dict], keyword_weights: Dict[str, float]):
        self.sources = sources_config
        self.keyword_weights = keyword_weights
        self.session = requests.Session()
        self.session.timeout = 30
    
    def fetch_all(self) -> List[Dict[str, Any]]:
        all_items = []
        for source in self.sources:
            try:
                items = self._fetch_api(source)
                all_items.extend(items)
                print(f"[API] {source['name']}: {len(items)} 条")
            except Exception as e:
                print(f"[API ERROR] {source['name']}: {e}")
        return all_items
    
    def _fetch_api(self, source: Dict) -> List[Dict[str, Any]]:
        params = source.get("api_params", {})
        resp = self.session.get(source["url"], params=params)
        data = resp.json()
        items = []
        return items


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    import sys
    config = load_config(sys.argv[1] if len(sys.argv) > 1 else "sources.yaml")
    
    list_sources = []
    for key in ["gov_portals", "stats_sources", "think_tanks"]:
        list_sources.extend(config.get(key, []))
    
    fetcher = ListPageFetcher(list_sources, config.get("keyword_weights", {}))
    items = fetcher.fetch_all()
    
    for item in items:
        print(json.dumps(item, ensure_ascii=False))
