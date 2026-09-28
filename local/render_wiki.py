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

try:
    from kb_guard import normalize_keywords, check_links, vault_link_index, guard_page
except ImportError:                       # 兼容以不同 cwd 运行
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from kb_guard import normalize_keywords, check_links, vault_link_index, guard_page


_LINK_INDEX_CACHE = {"key": None, "idx": None}


def _guard(text, page_type, vault_path, refresh=False):
    """写入前闸门。链接索引按 vault 缓存，避免每页重建（O(n²)）。

    refresh=True 时强制重建索引——用于「本页依赖刚写入的页」的场景
    （如宏观页引用当日日更页，而日更页是在同一次渲染里刚生成的）。
    """
    key = str(vault_path)
    if refresh or _LINK_INDEX_CACHE.get("key") != key:
        _LINK_INDEX_CACHE["key"] = key
        _LINK_INDEX_CACHE["idx"] = vault_link_index(key)
    return guard_page(text, page_type, index=_LINK_INDEX_CACHE["idx"])

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
        # 关键词过规范化层：长句/重复/超量在这里被挡掉（见 kb_guard.normalize_keywords）
        keywords = normalize_keywords(keywords)
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
            # 注意：不要把 macro_diagnosis 的值当关键词——那些是 AI 生成的长句
            # （实测如「毕业生群体面临更激烈竞争，60%企业未完成招聘目标意味着机会分布碎片化」），
            # 塞进 frontmatter 会让 `关键词` 字段变成句子堆。长句归正文，见下方「核心结构性判断」。
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
        # 写入前过闸门：frontmatter 必填 + 行数 + 链接存在性。
        # 不合格不抛异常，只告警——单页隔离，不拖垮整轮渲染（AGENTS.md「批处理必须能隔离坏元素」）。
        _ok, _reasons = _guard(content, "concept", str(self.vault_path))
        if not _ok:
            print(f"[WIKI][GUARD] {filepath.name} 未过闸门（仍写入，请复核）：")
            for _r in _reasons[:5]:
                print(f"           - {_r}")
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
        # 这里原先写死 [[OSINT每日情报-{intel_id[:8]}]]，但日更页实际命名是 osint-YYYYMMDD，
        # 格式对不上 → 该段永远渲染成悬空链接（知识库体检实测报断链）。
        # 改为「目标页存在才写 wikilink，否则写纯文本」——宁可缺不可错（AGENTS.md）。
        # 用新鲜索引：日更页是在同一次渲染里刚写入的，缓存的旧索引里还没有它。
        _link_idx = vault_link_index(str(self.vault_path))
        _today_page = f"osint-{datetime.now():%Y%m%d}"
        _today_ok, _ = check_links(f"[[{_today_page}]]", index=_link_idx)
        if _today_ok:
            lines.append(f"- [[{_today_page}]] —— 本页素材来源（{len(relevant)} 条相关情报）")
        else:
            # 日更页尚未生成时，逐条列出来源编号，不留悬空链接
            for a in relevant[:10]:
                lines.append(f"- OSINT 每日情报 {str(a.get('intel_id', ''))[:8]} ({a.get('confidence', 0)}/10)")
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
        _ok, _reasons = _guard(new_content, "concept", str(self.vault_path))
        if not _ok:
            print(f"[WIKI][GUARD] {filepath.name} 未过闸门（仍写入，请复核）：")
            for _r in _reasons[:5]:
                print(f"           - {_r}")
        filepath.write_text(new_content, encoding="utf-8")
        print(f"[WIKI] 概念页更新: {filepath}")
        return str(filepath)
    
    def update_index(self, new_links: List[str]):
        """把新概念页登记进 index.md 的 AUTO 区。

        两处修正（2026-09-21）：
        1. **写入前重读**：原先依赖 __init__ 时缓存的 self.existing_links，但渲染期间
           kb_linker 可能已改过 index.md（第三方/插件也可能改），用旧缓存回写会丢链接。
        2. **写 AUTO 区**：index.md 改为「手工区 + AUTO 标记区」结构后，概念页登记应
           落在 <!-- BEGIN AUTO: concepts --> 与 <!-- END AUTO: concepts --> 之间，
           不再往「## 概念」标题下硬插（那会破坏自动区边界）。
        """
        if not self.index_file.exists():
            return

        content = self.index_file.read_text(encoding="utf-8")
        begin = "<!-- BEGIN AUTO: concepts -->"
        end = "<!-- END AUTO: concepts -->"
        if begin not in content or end not in content:
            # index.md 还没迁移到 AUTO 结构：退化为「追加到文件末尾」，不硬插标题下
            add = [f"- [[{l}]]" for l in new_links if f"[[{l}]]" not in content]
            if add:
                content = content.rstrip() + "\n\n" + "\n".join(add) + "\n"
                self.index_file.write_text(content, encoding="utf-8")
                print(f"[WIKI] index.md 追加 {len(add)} 个链接（未启用 AUTO 区）")
            return

        head, rest = content.split(begin, 1)
        mid, tail = rest.split(end, 1)
        existing = set(re.findall(r"\[\[([^\]\|]+)", mid))
        add = [l for l in new_links if l not in existing]
        if add:
            mid = mid.rstrip("\n") + "\n" + "\n".join(f"- [[{l}]]" for l in add) + "\n"
            self.index_file.write_text(head + begin + mid + end + tail, encoding="utf-8")
            print(f"[WIKI] index.md 更新: 新增 {len(add)} 个链接")
        else:
            print("[WIKI] index.md 无需更新（链接已存在）")

    def append_log(self, action: str, details: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = f"\n- {now} | {action} | {details}"
        # 方法式 API（AGENTS.md：不要用 open(变量)，Mimosa 会按路径穿越拦截）
        with self.log_file.open("a", encoding="utf-8") as f:
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
