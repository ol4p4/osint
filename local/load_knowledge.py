#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - 知识库加载器
解析 Obsidian 知识库，为 AI 分析提供 RAG 上下文
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class KnowledgeNode:
    name: str
    type: str
    path: Path
    content: str
    frontmatter: Dict
    links: List[str]
    tags: List[str]
    keywords: List[str]


class KnowledgeBase:
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.wiki_path = self.vault_path / "wiki"
        self.concepts_dir = self.wiki_path / "concepts"
        self.entities_dir = self.wiki_path / "entities"
        self.comparisons_dir = self.wiki_path / "comparisons"
        self.queries_dir = self.wiki_path / "queries"
        self.raw_dir = self.wiki_path / "raw"
        self.index_file = self.wiki_path / "index.md"
        self.log_file = self.wiki_path / "log.md"
        
        self.nodes: Dict[str, KnowledgeNode] = {}
        self.index_links: List[str] = []
    
    def load_all(self) -> Dict[str, Any]:
        self._load_index()
        self._load_directory(self.concepts_dir, "concept")
        self._load_directory(self.entities_dir, "entity")
        self._load_directory(self.comparisons_dir, "comparison")
        self._load_directory(self.queries_dir, "query")
        
        return {
            "total_nodes": len(self.nodes),
            "by_type": self._count_by_type(),
            "concepts": [n for n in self.nodes.values() if n.type == "concept"],
            "entities": [n for n in self.nodes.values() if n.type == "entity"],
            "comparisons": [n for n in self.nodes.values() if n.type == "comparison"],
            "queries": [n for n in self.nodes.values() if n.type == "query"],
            "index_links": self.index_links,
            "macro_concepts": self._get_macro_concepts()
        }
    
    def _load_index(self):
        if self.index_file.exists():
            content = self.index_file.read_text(encoding="utf-8")
            self.index_links = re.findall(r"\[\[([^\]]+)\]\]", content)
    
    def _load_directory(self, dir_path: Path, default_type: str):
        if not dir_path.exists():
            return
        for md_file in dir_path.glob("*.md"):
            try:
                node = self._parse_markdown(md_file, default_type)
                if node:
                    self.nodes[node.name] = node
            except Exception as e:
                print(f"[KB] 解析失败 {md_file}: {e}")
    
    def _parse_markdown(self, file_path: Path, default_type: str) -> Optional[KnowledgeNode]:
        content = file_path.read_text(encoding="utf-8")
        
        frontmatter = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                import yaml
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                    body = parts[2]
                except:
                    pass
        
        links = re.findall(r"\[\[([^\]]+)\]\]", content)
        
        name = frontmatter.get("title", file_path.stem)
        node_type = frontmatter.get("type", default_type)
        tags = frontmatter.get("tags", [])
        keywords = frontmatter.get("关键词", [])
        
        return KnowledgeNode(
            name=name,
            type=node_type,
            path=file_path,
            content=body.strip(),
            frontmatter=frontmatter,
            links=links,
            tags=tags,
            keywords=keywords
        )
    
    def _count_by_type(self) -> Dict[str, int]:
        counts = {}
        for node in self.nodes.values():
            counts[node.type] = counts.get(node.type, 0) + 1
        return counts
    
    def _get_macro_concepts(self) -> List[str]:
        macro_keywords = [
            "宏观", "政治经济", "积累制度", "空间修正", "国家市场", "阶级", "利益集团",
            "劳动力市场", "技能溢价", "资产价格", "代际", "再生产", "区域选择",
            "能源", "通胀", "货币政策", "财政政策", "产业政策", "就业", "失业",
            "央企", "国企", "基建", "新能源", "核电", "电网"
        ]
        macro_nodes = []
        for node in self.nodes.values():
            if node.type == "concept":
                text = (node.name + " " + " ".join(node.tags) + " " + " ".join(node.keywords)).lower()
                if any(kw in text for kw in macro_keywords):
                    macro_nodes.append(node.name)
        return macro_nodes
    
    def get_relevant_context(self, intel_item: Dict, max_chars: int = 3000) -> str:
        keywords = intel_item.get("keywords_hit", [])
        entities = intel_item.get("entities", [])
        all_terms = keywords + entities
        
        scored_nodes = []
        for node in self.nodes.values():
            if node.type not in ["concept", "entity", "comparison"]:
                continue
            score = 0
            node_text = (node.name + " " + " ".join(node.tags) + " " + " ".join(node.keywords) + " " + node.content[:500]).lower()
            for term in all_terms:
                if term.lower() in node_text:
                    score += 2
            for link in node.links:
                if any(term.lower() in link.lower() for term in all_terms):
                    score += 1
            if score > 0:
                scored_nodes.append((score, node))
        
        scored_nodes.sort(key=lambda x: -x[0])
        
        context_parts = []
        total_chars = 0
        for score, node in scored_nodes[:5]:
            snippet = f"## [[{node.name}]]\n{node.content[:800]}"
            if total_chars + len(snippet) > max_chars:
                break
            context_parts.append(snippet)
            total_chars += len(snippet)
        
        return "\n\n".join(context_parts)
    
    def get_macro_framework_context(self) -> str:
        macro_names = self._get_macro_concepts()
        if not macro_names:
            return ""
        
        context_parts = []
        for name in macro_names[:8]:
            node = self.nodes.get(name)
            if node:
                context_parts.append(f"## [[{node.name}]]\n{node.content[:1000]}")
        
        return "\n\n".join(context_parts)


def load_knowledge(vault_path: str) -> KnowledgeBase:
    kb = KnowledgeBase(vault_path)
    kb.load_all()
    return kb


if __name__ == "__main__":
    import sys
    vault = sys.argv[1] if len(sys.argv) > 1 else r"D:\Codex输出\视频知识库"
    kb = load_knowledge(vault)
    idx = kb.load_all()
    print(f"知识库加载完成: {idx['total_nodes']} 个节点")
    print(f"  概念: {len(idx['concepts'])}")
    print(f"  实体: {len(idx['entities'])}")
    print(f"  对比: {len(idx['comparisons'])}")
    print(f"  问题: {len(idx['queries'])}")
    print(f"宏观概念: {idx['macro_concepts'][:10]}")
