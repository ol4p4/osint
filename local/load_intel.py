#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 情报加载器
支持从 GitHub Artifact、私有 Gist、本地文件加载情报
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml


def load_from_local(date_str: str = None, cache_dir: str = None) -> List[Dict[str, Any]]:
    if cache_dir is None:
        cache_dir = r"D:\osint\data\cache"
    
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    
    paths = [
        Path(cache_dir) / f"intel_{date_str}.jsonl",
        Path(cache_dir) / f"intel_{date_str}.json",
        Path(".") / f"intel_{date_str}.jsonl",
        Path(".") / f"intel_{date_str}.json",
    ]
    
    for p in paths:
        if p.exists():
            items = []
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
            print(f"[LOAD] 本地加载 {len(items)} 条: {p}")
            return items
    
    print(f"[LOAD] 本地未找到 {date_str} 的情报文件")
    return []


def load_from_gist(gist_id: str, date_str: str = None, token: str = None) -> List[Dict[str, Any]]:
    if not gist_id:
        return []
    
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    
    filename = f"intel_{date_str}.jsonl"
    
    try:
        import requests
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[LOAD] Gist 获取失败: {resp.status_code}")
            return []
        
        data = resp.json()
        if filename not in data.get("files", {}):
            print(f"[LOAD] Gist 中无文件: {filename}")
            return []
        
        content = data["files"][filename]["content"]
        items = []
        for line in content.strip().split("\n"):
            if line.strip():
                items.append(json.loads(line))
        print(f"[LOAD] Gist 加载 {len(items)} 条: {gist_id}/{filename}")
        return items
    except Exception as e:
        print(f"[LOAD] Gist 加载异常: {e}")
        return []


def load_from_artifact(github_repo: str, artifact_name: str = "intel-daily", token: str = None) -> List[Dict[str, Any]]:
    if not github_repo:
        return []
    
    try:
        cmd = ["gh", "run", "download", "-R", github_repo, "-n", artifact_name, "--dir", "temp_artifact"]
        env = os.environ.copy()
        if token:
            env["GH_TOKEN"] = token
        
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if result.returncode != 0:
            print(f"[LOAD] gh 下载失败: {result.stderr}")
            return []
        
        items = []
        for p in Path("temp_artifact").rglob("intel_*.jsonl"):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
        
        import shutil
        shutil.rmtree("temp_artifact", ignore_errors=True)
        
        print(f"[LOAD] Artifact 加载 {len(items)} 条: {github_repo}/{artifact_name}")
        return items
    except Exception as e:
        print(f"[LOAD] Artifact 加载异常: {e}")
        return []


def load_intel(config: Dict, date_str: str = None) -> List[Dict[str, Any]]:
    cloud_cfg = config.get("mode", {}).get("cloud_fetch", {})
    method = cloud_cfg.get("method", "local")
    
    if method == "gist":
        items = load_from_gist(
            cloud_cfg.get("gist_id", ""),
            date_str,
            os.environ.get("GIST_TOKEN") or cloud_cfg.get("gist_token")
        )
        if items:
            return items
    
    if method == "artifact":
        items = load_from_artifact(
            cloud_cfg.get("github_repo", ""),
            cloud_cfg.get("artifact_name", "intel-daily"),
            os.environ.get("GH_TOKEN")
        )
        if items:
            return items
    
    paths_cfg = config.get("paths", {})
    return load_from_local(date_str, paths_cfg.get("intel_cache_dir"))


def load_persona(persona_path: str) -> str:
    with open(persona_path, "r", encoding="utf-8") as f:
        return f.read()


def load_weights(weights_path: str) -> Dict:
    with open(weights_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_knowledge_index(vault_path: str) -> Dict[str, Any]:
    index_file = Path(vault_path) / "wiki" / "index.md"
    if not index_file.exists():
        return {"concepts": [], "entities": [], "comparisons": [], "queries": []}
    
    content = index_file.read_text(encoding="utf-8")
    import re
    links = re.findall(r"\[\[([^\]]+)\]\]", content)
    
    concepts = [l for l in links if not l.startswith(("实体-", "对比-", "问题-"))]
    entities = [l.replace("实体-", "") for l in links if l.startswith("实体-")]
    comparisons = [l.replace("对比-", "") for l in links if l.startswith("对比-")]
    queries = [l.replace("问题-", "") for l in links if l.startswith("问题-")]
    
    return {
        "concepts": concepts,
        "entities": entities,
        "comparisons": comparisons,
        "queries": queries,
        "raw_links": links
    }


def load_concept_pages(vault_path: str, concept_names: List[str]) -> Dict[str, str]:
    concepts_dir = Path(vault_path) / "wiki" / "concepts"
    pages = {}
    
    for name in concept_names:
        candidates = [
            concepts_dir / f"{name}.md",
            concepts_dir / f"{name.lower()}.md",
            concepts_dir / f"{name.replace(chr(32), chr(45)).lower()}.md",
        ]
        for c in candidates:
            if c.exists():
                pages[name] = c.read_text(encoding="utf-8")
                break
    
    return pages


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    items = load_intel(config, args.date)
    print(f"总计加载: {len(items)} 条")
    for item in items[:3]:
        print(f"  - {item.get('title', '')[:60]} (score: {item.get('final_score', 0)})")

