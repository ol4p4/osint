#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端情报官 - 清洗、去重、基础评分管道
合并所有源的输出，输出最终 intel_YYYYMMDD.jsonl
"""

import json
import yaml
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Set
from collections import defaultdict
import simhash
import re


class SimHashDedup:
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.hashes: List[int] = []
        self.id_map: Dict[int, str] = {}
    
    def add(self, text: str, item_id: str) -> bool:
        h = self._simhash(text)
        for existing_h in self.hashes:
            if self._hamming_distance(h, existing_h) <= self.threshold:
                return False
        self.hashes.append(h)
        self.id_map[h] = item_id
        return True
    
    def _simhash(self, text: str) -> int:
        tokens = self._tokenize(text)
        if not tokens:
            return 0
        v = [0] * 64
        for token, weight in tokens:
            h = hashlib.md5(token.encode()).hexdigest()
            bits = bin(int(h, 16))[2:].zfill(64)[:64]
            for i, bit in enumerate(bits):
                v[i] += weight if bit == "1" else -weight
        fingerprint = 0
        for i, val in enumerate(v):
            if val > 0:
                fingerprint |= (1 << i)
        return fingerprint
    
    def _tokenize(self, text: str) -> List[tuple]:
        text = re.sub(r"[^\u4e00-\u9fff\w]", " ", text)
        tokens = []
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                tokens.append((ch, 1.0))
            else:
                for w in text.split():
                    if w:
                        tokens.append((w.lower(), 1.0))
        return tokens[:200]
    
    def _hamming_distance(self, a: int, b: int) -> int:
        return bin(a ^ b).count("1")


class TitleDedup:
    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold
        self.titles: List[Set[str]] = []
        self.id_map: Dict[str, str] = {}
    
    def add(self, title: str, item_id: str) -> bool:
        title_set = set(self._tokenize_title(title))
        title_key = " ".join(sorted(title_set))
        
        if title_key in self.id_map:
            return False
        
        for existing_set in self.titles:
            if self._jaccard(title_set, existing_set) >= self.threshold:
                return False
        
        self.titles.append(title_set)
        self.id_map[title_key] = item_id
        return True
    
    def _tokenize_title(self, title: str) -> List[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", title.lower())
        return [t for t in tokens if len(t) > 1]
    
    def _jaccard(self, a: Set[str], b: Set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)


class TimeDecayScorer:
    def __init__(self, half_life_hours: float = 48, max_age_hours: float = 168, min_score: float = 0.1):
        self.half_life = half_life_hours
        self.max_age = max_age_hours
        self.min_score = min_score
    
    def score(self, published_at: str, fetched_at: str = None) -> float:
        try:
            pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")) if fetched_at else datetime.now(timezone.utc)
            age_hours = (now - pub).total_seconds() / 3600
            
            if age_hours <= 0:
                return 1.0
            if age_hours >= self.max_age:
                return self.min_score
            
            decay = 0.5 ** (age_hours / self.half_life)
            return max(decay, self.min_score)
        except:
            return 0.5


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_items(items: List[Dict]) -> List[Dict]:
    cleaned = []
    for item in items:
        item = item.copy()
        item["title"] = clean_text(item.get("title", ""))
        raw_content = item.get("content", "") or item.get("content_preview", "") or item.get("content_full", "") or item.get("cn_summary", "")
        item["content"] = clean_text(raw_content)
        item["summary"] = clean_text(item.get("summary", ""))
        zh_chars = len(re.findall(r"[一-鿿]", item["content"]))
        total_chars = len(item["content"])
        item["lang"] = "zh" if zh_chars / max(total_chars, 1) > 0.3 else "other"
        has_cn = bool(item.get("cn_title") or item.get("cn_summary"))
        if (item["lang"] == "zh" and len(item["content"]) >= 100) or has_cn or (total_chars >= 200):
            cleaned.append(item)
    return cleaned


def dedup_items(items: List[Dict], config: Dict) -> List[Dict]:
    dedup_cfg = config.get("dedup", {})
    simhash_thresh = dedup_cfg.get("simhash_threshold", 3)
    jaccard_thresh = dedup_cfg.get("title_jaccard_threshold", 0.7)
    url_exact = dedup_cfg.get("url_exact_match", True)
    
    simhash_dedup = SimHashDedup(simhash_thresh)
    title_dedup = TitleDedup(jaccard_thresh)
    seen_urls: Set[str] = set()
    
    unique_items = []
    for item in items:
        url = item.get("url", "")
        if url_exact and url in seen_urls:
            continue
        
        content_for_hash = item.get("title", "") + " " + item.get("content", "")
        if not simhash_dedup.add(content_for_hash, item["id"]):
            continue
        
        if not title_dedup.add(item.get("title", ""), item["id"]):
            continue
        
        seen_urls.add(url)
        unique_items.append(item)
    
    print(f"[DEDUP] {len(items)} -> {len(unique_items)}")
    return unique_items


def score_items(items: List[Dict], config: Dict) -> List[Dict]:
    weights_cfg = config
    time_decay_cfg = weights_cfg.get("time_decay", {})
    scorer = TimeDecayScorer(
        half_life_hours=time_decay_cfg.get("half_life_hours", 48),
        max_age_hours=time_decay_cfg.get("max_age_hours", 168),
        min_score=time_decay_cfg.get("min_score", 0.1)
    )
    
    source_weights = weights_cfg.get("source_weights", {})
    
    for item in items:
        src_w = source_weights.get(item.get("category", "rss"), 1.0)
        time_w = scorer.score(item.get("published_at", ""), item.get("fetched_at", ""))
        kw_score = item.get("base_score", 0.5) / max(src_w, 0.001)
        kw_score = min(max(kw_score, 0), 1)
        
        final_score = src_w * kw_score * time_w
        item["final_score"] = round(min(final_score, 1.0), 3)
        item["score_breakdown"] = {
            "source_weight": src_w,
            "keyword_score": round(kw_score, 3),
            "time_decay": round(time_w, 3)
        }
    
    items.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return items


def filter_by_threshold(items: List[Dict], config: Dict) -> List[Dict]:
    thresholds = config.get("thresholds", {})
    high = thresholds.get("high_priority", 0.75)
    medium = thresholds.get("medium_priority", 0.50)
    low = thresholds.get("low_priority", 0.30)
    archive = thresholds.get("archive_only", 0.15)
    
    for item in items:
        score = item.get("final_score", 0)
        if score >= high:
            item["priority"] = "high"
        elif score >= medium:
            item["priority"] = "medium"
        elif score >= low:
            item["priority"] = "low"
        elif score >= archive:
            item["priority"] = "archive"
        else:
            item["priority"] = "drop"
    
    filtered = [i for i in items if i["priority"] != "drop"]
    print(f"[FILTER] {len(items)} -> {len(filtered)} (high:{sum(1 for i in filtered if i['priority']=='high')}, medium:{sum(1 for i in filtered if i['priority']=='medium')}, low:{sum(1 for i in filtered if i['priority']=='low')}, archive:{sum(1 for i in filtered if i['priority']=='archive')})")
    return filtered


def pipeline(input_jsonl: str, output_jsonl: str, config_path: str):
    config = load_config(config_path)
    
    items = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    
    print(f"[PIPELINE] 输入: {len(items)} 条")
    
    items = clean_items(items)
    print(f"[CLEAN] {len(items)} 条")
    
    items = dedup_items(items, config)
    
    items = score_items(items, config)
    
    items = filter_by_threshold(items, config)

    Path(output_jsonl).write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8")
    
    print(f"[PIPELINE] 输出: {len(items)} 条 -> {output_jsonl}")
    return items


if __name__ == "__main__":
    import sys
    input_file = sys.argv[1] if len(sys.argv) > 1 else "intel_raw.jsonl"
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"intel_{datetime.now().strftime('%Y%m%d')}.jsonl"
    config_file = sys.argv[3] if len(sys.argv) > 3 else "weights.yaml"
    pipeline(input_file, output_file, config_file)
