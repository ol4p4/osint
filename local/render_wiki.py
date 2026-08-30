#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地参谋长 - Obsidian 知识库入库生成器
生成符合规范的概念/实体/问题页，自动双向链接、更新 index.md 和 log.md
"""

import json
import re
import yaml
from datetime import datetime
from typing import List, Dict, Any, Set
from pathlib import Path
from collections import defaultdict

# AI 四维诊断键 → 知识库固定维度页（长句分析汇入页内，不再生成碎片文件）
_DIM_PAGE = {
    "accumulation_node": "宏观-积累制度与劳动力市场",
    "spatial_layer": "宏观-空间修正与区域选择",
    "state_market_shift": "宏观-国家市场边界迁移史",
    "class_interest": "宏观-青年劳动力再生产成本",
}


class WikiRenderer:
    def __init__(self, config: Dict, vault_path: str):
        self.config = config
        self.vault_path = Path(vault_path)
        self.wiki_path = self.vault_path / "wiki"
        self.concepts_dir = self.wiki_path / "concepts"
        self.entities_dir = self.wiki_path / "entities"
        self.comparisons_dir = self.wiki_path / "comparisons"
        self.queries_dir = self.wiki_path / "queries"
        self.index_file = self.wiki_path / "index.md"
        self.log_file = self.wiki_path / "log.md"
        self.schema_file = self.wiki_path / "SCHEMA.md"
        
        self.valid_tags = self._load_schema_tags()
        self.existing_links = self._load_index_links()
    
    def _load_schema_tags(self) -> Set[str]:
        tags = {"OSINT", "宏观分析", "政策研判", "情报分析", "决策支持", "AI分析"}
        if self.schema_file.exists():
            content = self.schema_file.read_text(encoding="utf-8")
            in_tags = False
            for line in content.split("\n"):
                if "标签表" in line:
                    in_tags = True
                    continue
                if in_tags and line.startswith("##"):
                    break
                if in_tags:
                    matches = re.findall(r"`([^`]+)`", line)
                    tags.update(matches)
        return tags
    
    def _load_index_links(self) -> Set[str]:
        links = set()
        if self.index_file.exists():
            content = self.index_file.read_text(encoding="utf-8")
            links = set(re.findall(r"\[\[([^\]]+)\]\]", content))
        return links
    
    def _sanitize_filename(self, title: str) -> str:
        name = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", title)
        name = re.sub(r"\s+", "-", name.strip()).lower()
        return name[:80] if len(name) > 80 else name
    
    def _generate_frontmatter(self, title: str, page_type: str, tags: List[str], keywords: List[str], sources: List[str]) -> str:
        now = datetime.now().strftime("%Y-%m-%d")
        fm = {"title": title, "created": now, "updated": now, "type": page_type, "tags": tags, "关键词": keywords, "sources": sources}
        return "---\n" + yaml.dump(fm, allow_unicode=True, sort_keys=False) + "---\n"
    
    def render_daily_concept(self, analyses, intel_items: List[Dict], date_str: str) -> str:
        from dataclasses import asdict
        analyses = [asdict(a) if hasattr(a, "__dataclass_fields__") else a for a in analyses]
        title = f"OSINT 每日情报 {date_str}"
        filename = f"osint-{date_str}.md"
        filepath = self.concepts_dir / filename
        
        all_tags = {"OSINT", "情报分析", "每日简报"}
        all_keywords = set()
        all_sources = set()
        all_links = set()
        
        for a in analyses:
            intel = next((i for i in intel_items if i["id"] == a["intel_id"]), {})
            all_sources.add(intel.get("source_name", ""))
            all_keywords.update(intel.get("keywords_hit", []))
            all_keywords.update(a.get("macro_diagnosis", {}).values())
            all_links.update(a.get("knowledge_links", []))
        
        valid_tags = [t for t in all_tags if t in self.valid_tags or len(t) < 20]
        
        lines = []
        lines.append(self._generate_frontmatter(title, "concept", valid_tags, list(all_keywords)[:20], list(all_sources)))
        lines.append(f"# {title}")
        lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 情报条数：{len(intel_items)} | 深度分析：{len(analyses)}")
        lines.append("")
        
        lines.append("## 执行摘要")
        high = [a for a in analyses if a.get("confidence", 0) >= 7]
        medium = [a for a in analyses if 4 <= a.get("confidence", 0) < 7]
        lines.append(f"- 🔴 高置信度：{len(high)} 条核心结构性信号")
        lines.append(f"- 🟡 中置信度：{len(medium)} 条重要趋势信号")
        lines.append("")
        
        lines.append("## 核心结构性判断")
        themes = defaultdict(list)
        for a in high + medium:
            for key, val in a.get("macro_diagnosis", {}).items():
                if val and val != "未识别":
                    themes[key].append(val)
        for theme, vals in themes.items():
            unique = list(set(vals))[:3]
            joined = "; ".join(unique)
            lines.append(f"- **{theme}**：{joined}")
        lines.append("")
        
        lines.append("## 详细情报研判")
        for a in sorted(analyses, key=lambda x: -x.get("confidence", 0)):
            intel = next((i for i in intel_items if i["id"] == a["intel_id"]), {})
            conf = a.get("confidence", 0)
            icon = "🔴" if conf >= 7 else "🟡" if conf >= 4 else "🟢"
            
            lines.append(f"### {icon} {intel.get('title', a['intel_id'])}")
            lines.append(f"> 来源：{intel.get('source_name', '')} | 置信度：{conf}/10")
            lines.append("")
            
            lines.append("**四维诊断**")
            md = a.get("macro_diagnosis", {})
            lines.append(f"- 积累环节：{md.get('accumulation_node', '未识别')}")
            lines.append(f"- 空间层级：{md.get('spatial_layer', '未识别')}")
            lines.append(f"- 国家-市场：{md.get('state_market_shift', '未识别')}")
            lines.append(f"- 利益集团：{md.get('class_interest', '未识别')}")
            lines.append("")
            
            lines.append("**结构性含义**")
            lines.append(a.get("structural_implication", "无"))
            lines.append("")
            
            pas = a.get("personal_action_space", {})
            wm = pas.get("window_months", 18)
            lines.append(f"**行动空间（{wm}个月窗口）**")
            for m in pas.get("concrete_moves", []):
                # AI 返回的 concrete_moves 可能是字符串或对象，两种都兼容
                if isinstance(m, str):
                    lines.append(f"- ✅ {m}")
                    continue
                lines.append(f"- ✅ {m.get('action', '')}")
                lines.append(f"  - 理由：{m.get('rationale', '')}")
                lines.append(f"  - 风险：{m.get('risk', '')}")
            if pas.get("avoid_traps"):
                joined = "；".join(str(t) for t in pas["avoid_traps"])
                lines.append(f"- ⚠️ 避坑：{joined}")
            if pas.get("signals_to_watch"):
                joined = "；".join(str(s) for s in pas["signals_to_watch"])
                lines.append(f"- 👁️ 观测：{joined}")
            lines.append("")
            
            if a.get("knowledge_links"):
                joined = "、".join(a["knowledge_links"])
                lines.append(f"**知识库关联**：{joined}")
                lines.append("")
            
            if a.get("contradictions"):
                lines.append(f"> ⚠️ **待验证**：{a['contradictions']}")
                lines.append("")
            
            lines.append("---")
            lines.append("")
        
        if all_links:
            lines.append("## 知识库链接索引")
            for link in sorted(all_links):
                lines.append(f"- {link}")
        
        content = "\n".join(lines)
        filepath.write_text(content, encoding="utf-8")
        print(f"[WIKI] 概念页生成: {filepath}")
        return str(filepath)
    
    def render_macro_concepts(self, analyses) -> List[str]:
        from dataclasses import asdict
        analyses = [asdict(a) if hasattr(a, "__dataclass_fields__") else a for a in analyses]
        macro_concepts = {
            "宏观-积累制度与劳动力市场": {"tags": ["宏观分析", "政治经济学", "劳动力市场"], "keywords": ["积累制度", "资本循环", "生产", "实现", "分配", "再生产", "技能溢价"]},
            "宏观-空间修正与区域选择": {"tags": ["宏观分析", "政治经济学", "区域经济"], "keywords": ["空间修正", "中心-外围", "特区", "都市圈", "区域选择", "人才政策"]},
            "宏观-国家市场边界迁移史": {"tags": ["宏观分析", "政治经济学", "产业政策"], "keywords": ["国家-市场边界", "国家进场", "市场退场", "试点先行", "产业政策", "监管"]},
            "宏观-青年劳动力再生产成本": {"tags": ["宏观分析", "政治经济学", "劳动力市场", "青年就业"], "keywords": ["再生产成本", "青年失业", "技能投资", "通胀对冲", "代际财富"]},
        }
        
        for a in analyses:
            md = a.get("macro_diagnosis", {})
            for dim, val in md.items():
                if val and val != "未识别":
                    # 2026-08-30 修复：AI 长句 val 不再进文件名（曾产生 79 个超长名文件污染知识库），
                    # 一律汇入 4 个固定维度页，长句作为页内更新素材
                    concept_name = _DIM_PAGE.get(dim, f"宏观-{dim}")
                    if concept_name not in macro_concepts:
                        macro_concepts[concept_name] = {"tags": ["宏观分析", "政治经济学"], "keywords": [dim]}
        
        created_files = []
        for concept_name, meta in macro_concepts.items():
            filepath = self._render_or_update_concept(concept_name, meta, analyses)
            if filepath:
                created_files.append(filepath)
        
        return created_files
    
    def _render_or_update_concept(self, concept_name: str, meta: Dict, analyses: List[Dict]) -> str:
        filename = self._sanitize_filename(concept_name) + ".md"
        filepath = self.concepts_dir / filename
        
        relevant = []
        for a in analyses:
            md = a.get("macro_diagnosis", {})
            dim = concept_name.replace("宏观-", "").split("-")[0]
            for v in md.values():
                if dim in str(v):
                    relevant.append(a)
                    break
        
        tags = list(set(meta.get("tags", []) + ["宏观分析", "政治经济学"]))
        valid_tags = [t for t in tags if t in self.valid_tags or len(t) < 20]
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        
        lines = []
        lines.append(self._generate_frontmatter(concept_name, "concept", valid_tags, meta.get("keywords", []), [f"OSINT分析-{today}"]))
        lines.append(f"# {concept_name}")
        lines.append(f"> 更新时间：{now}")
        lines.append("")
        
        lines.append("## 定义与分析框架")
        if "积累制度" in concept_name:
            lines.append("**积累制度视角**关注政策/事件作用于资本循环的哪个环节：")
            lines.append("- **生产端**：产业补贴、技术升级、要素成本变化")
            lines.append("- **实现端**：消费券、出口政策、内需扩大")
            lines.append("- **分配端**：税收改革、社保调整、收入分配")
            lines.append("- **再生产端**：教育/培训、劳动力技能、代际流动")
        elif "空间修正" in concept_name:
            lines.append("**空间修正视角**关注中心-外围结构、特区实验、城市群协同：")
            lines.append("- **中心**：一线/强二线城市，高价值活动聚集")
            lines.append("- **外围**：三四线/县域，承接产业转移、提供要素")
            lines.append("- **特区/园区**：政策实验场，红利窗口期、准入门槛")
            lines.append("- **都市圈**：跨行政区协同，通勤/产业/公服一体化")
        elif "国家市场边界" in concept_name:
            lines.append("**国家-市场边界视角**关注国家力量与市场力量的边界迁移：")
            lines.append("- **国家进场**：战略性行业国有化、产业引导基金、基建投资、监管红线")
            lines.append("- **市场退场**：竞争性领域开放、民营准入、服务业放开")
            lines.append("- **边界模糊**：国企混改、平台经济监管、数据要素市场")
            lines.append("- **试点先行**：自贸区、雄安、海南、大湾区等先行先试")
        elif "青年劳动力" in concept_name:
            lines.append("**青年劳动力再生产成本**关注年轻一代技能获取、就业、资产积累的结构性约束：")
            lines.append("- **技能投资回报率**：学历贬值、培训成本、证书含金量")
            lines.append("- **就业匹配度**：专业对口率、技能错配、结构性失业")
            lines.append("- **资产积累能力**：房价/收入比、金融资产获取、代际传递")
            lines.append("- **通胀对冲**：实际工资、储蓄贬值、抗周期资产配置")
        else:
            lines.append(f"基于 OSINT 情报分析提炼的宏观概念：{concept_name}")
        lines.append("")
        
        if relevant:
            lines.append("## 当前理解（基于最新情报研判）")
            for a in relevant[:5]:
                md = a.get("macro_diagnosis", {})
                impl = a.get("structural_implication", "")
                if impl:
                    dim_label = md.get("accumulation_node", "未知")
                    spa_label = md.get("spatial_layer", "未知")
                    lines.append(f"- **{dim_label} / {spa_label}**：{impl[:200]}")
            lines.append("")
        
        lines.append("## 相关情报引用")
        for a in relevant[:10]:
            lines.append(f"- [[OSINT每日情报-{a.get('intel_id', '')[:8]}]] ({a.get('confidence', 0)}/10)")
        lines.append("")
        
        lines.append("## 开放问题")
        lines.append("- 该维度的政策传导滞后期有多长？")
        lines.append("- 地方利益与中央意图的博弈如何演变？")
        lines.append("- 对普通青年劳动者的具体传导路径是什么？")
        lines.append("")
        
        lines.append("## 来源")
        lines.append(f"- OSINT 个人智库系统每日分析（{today}）")
        lines.append("")
        
        new_content = "\n".join(lines)
        filepath.write_text(new_content, encoding="utf-8")
        print(f"[WIKI] 概念页更新: {filepath}")
        return str(filepath)
    
    def update_index(self, new_links: List[str]):
        if not self.index_file.exists():
            return
        
        content = self.index_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        
        concept_section = -1
        for i, line in enumerate(lines):
            if "## 概念" in line:
                concept_section = i
                break
        
        if concept_section >= 0:
            insert_at = concept_section + 1
            while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].startswith("##"):
                insert_at += 1
            
            for link in new_links:
                if link not in self.existing_links:
                    link_line = f"- [[{link}]]"
                    if link_line not in content:
                        lines.insert(insert_at, link_line)
                        insert_at += 1
                        self.existing_links.add(link)
            
            self.index_file.write_text("\n".join(lines), encoding="utf-8")
            print(f"[WIKI] index.md 更新: 新增 {len(new_links)} 个链接")
    
    def append_log(self, action: str, details: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = f"\n- {now} | {action} | {details}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
        print(f"[WIKI] log.md 记录: {action}")


from dataclasses import asdict
def render_wiki(analyses, intel_items: List[Dict], date_str: str, config: Dict) -> Dict[str, Any]:
    vault_path = config.get("knowledge_base", {}).get("vault_path", r"D:\Codex输出\视频知识库")
    renderer = WikiRenderer(config, vault_path)
    
    results = {"concept_pages": [], "macro_pages": [], "index_updated": False, "logged": False}
    
    daily_page = renderer.render_daily_concept(analyses, intel_items, date_str)
    results["concept_pages"].append(daily_page)
    
    macro_pages = renderer.render_macro_concepts(analyses)
    results["macro_pages"] = macro_pages
    
    all_new_links = []
    daily_link = f"osint-{date_str}"
    if daily_link not in renderer.existing_links:
        all_new_links.append(daily_link)
    for mp in macro_pages:
        link_name = Path(mp).stem
        if link_name not in renderer.existing_links:
            all_new_links.append(link_name)
    
    if all_new_links:
        renderer.update_index(all_new_links)
        results["index_updated"] = True
    
    renderer.append_log("OSINT入库", f"生成每日情报页 {daily_link} + {len(macro_pages)} 个宏观概念页")
    results["logged"] = True
    
    return results


def generate_wiki_from_files(analysis_file: str, intel_file: str, config: Dict):
    analyses = []
    with open(analysis_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                analyses.append(json.loads(line))
    intel_items = []
    with open(intel_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                intel_items.append(json.loads(line))
    date_str = datetime.now().strftime("%Y%m%d")
    return render_wiki(analyses, intel_items, date_str, config)


if __name__ == "__main__":
    import sys, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--intel", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    generate_wiki_from_files(args.analysis, args.intel, config)
