# -*- coding: utf-8 -*-
"""
Dialogue Engine - 5-round questioning to extract structured views
Modes: interactive (command line) / batch (read draft from Obsidian)
"""

import json, os, re, yaml, uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

QUESTION_ROUNDS = [
    {
        "round": 1,
        "name": "WHAT",
        "prompt": "你说的[观点]具体指什么？请从以下维度中选择或补充：经济结构、社会政策、制度设计、文化传统、人口趋势、国际关系。"
    },
    {
        "round": 2,
        "name": "WHY",
        "prompt": "你基于什么得出这个判断？是读过什么书、看过什么数据、还是个人观察和经历？请具体说明。"
    },
    {
        "round": 3,
        "name": "HOW",
        "prompt": "这个观点如何验证？什么数据出现能支持它？什么数据出现能反驳它？请给出具体的可观察指标。"
    },
    {
        "round": 4,
        "name": "WHEN",
        "prompt": "你认为这个判断的时间跨度是多长？什么时候能验证对错？给出一个到期日。"
    },
    {
        "round": 5,
        "name": "WHO",
        "prompt": "这个判断对谁影响最大？对你个人意味着什么？你会因此做出什么改变？"
    },
]

class DialogueEngine:
    def __init__(self, config, analyzer):
        self.config = config
        self.analyzer = analyzer
        paths = config.get("paths", {})
        self.output_dir = Path(paths.get("output_dir", r"D:\Codex输出\osint_卫星图"))
        self.idea_dir = Path(r"D:\Codex输出\视频知识库\00_收件箱\idea")
        self.idea_dir.mkdir(parents=True, exist_ok=True)
        self.dialogue_dir = self.output_dir / "dialogues"
        self.dialogue_dir.mkdir(parents=True, exist_ok=True)

    def run_interactive(self, initial_idea: str):
        """Interactive mode: ask 5 rounds and output observation card"""
        responses = []
        current_idea = initial_idea
        for round_info in QUESTION_ROUNDS:
            q = round_info["prompt"]
            # Replace [观点] with the current idea
            prompt = q.replace("[观点]", current_idea)
            print(f"\n=== Round {round_info['round']}: {round_info['name']} ===")
            print(prompt)
            resp = input("Your answer: ").strip()
            responses.append({
                "round": round_info["round"],
                "name": round_info["name"],
                "prompt": q,
                "answer": resp
            })
            current_idea = resp  # For next round, use answer as context
        
        # Generate observation card
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = re.sub(r'[^\w\s-]', '', initial_idea)[:50].strip()
        title = re.sub(r'\s+', '-', title)
        obsv_path = self.dialogue_dir / f"dialogue-{title}-{today}.md"
        
        md_lines = [
            f"# Dialogue Record - {initial_idea}",
            f"",
            f"Date: {today}",
            "",
        ]
        for r in responses:
            md_lines.append(f"### Round {r['round']}: {r['name']}")
            md_lines.append(f"Question: {r['prompt']}")
            md_lines.append(f"Answer: {r['answer']}")
            md_lines.append("")
        
        # Generate observation card structure
        md_lines.append("# Observation Card")
        md_lines.append("")
        md_lines.append(f"- **Initial Idea**: {initial_idea}")
        md_lines.append(f"- **Generated**: {today}")
        md_lines.append(f"- **Rounds**: {len(responses)}")
        md_lines.append("")
        
        with open(obsv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        
        # Also generate the observation card
        card_path = self.idea_dir / f"观点-{title}.md"
        card_lines = [
            f"# 观点卡",
            f"",
            f"- **标题**: {initial_idea}",
            f"- **来源**: 用户主动提出",
            f"- **状态**: active",
            f"- **生成日期**: {today}",
            f"- **对话记录**: [[dialogue-{title}-{today}.md]]",
            f"- **AI建议**: 已生成5轮追问，见对话记录",
            f"- **建议下一步**: 写入 views.yaml 并触发假设引擎",
            ""
        ]
        with open(card_path, "w", encoding="utf-8") as f:
            f.write("\n".join(card_lines))
        
        print(f"\n观点卡已生成: {card_path}")
        print(f"对话记录已保存: {obsv_path}")
        return responses

    def run_batch(self, draft_path: str):
        """Batch mode: read draft from Obsidian idea dir, ask questions, update"""
        with open(draft_path, "r", encoding="utf-8") as f:
            draft = f.read()
        
        # Check if already processed
        if "status: active" in draft:
            print("This draft has already been processed as an active view.")
            return
        
        # Extract initial idea (first 200 chars or until first newline)
        initial_idea = draft.split("\n")[0][:200] if draft else ""
        print(f"Processing draft: {initial_idea}")
        
        responses = self.run_interactive(initial_idea)
        
        # Update the draft file
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(draft + f"\n\n# Processed by Dialogue Engine on {datetime.now(timezone.utc).strftime("%Y-%m-%d")}\n")
            f.write(f"status: active\n")
        
        print(f"Draft updated with status: active")