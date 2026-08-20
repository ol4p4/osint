#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Staff Officer - AI Deep Analysis Engine"""

import json, os, re, yaml
from typing import List, Dict, Any
from dataclasses import dataclass, asdict
import urllib.request, urllib.error, uuid

@dataclass
class AnalysisResult:
    intel_id: str
    macro_diagnosis: Dict[str, str]
    structural_implication: str
    personal_action_space: Dict[str, Any]
    knowledge_links: List[str]
    confidence: int
    contradictions: str
    raw_reasoning: str

class MacroAnalyzer:
    def __init__(self, config, persona, knowledge_base):
        self.config = config
        self.persona = persona
        self.kb = knowledge_base
        api_cfg = config.get("api", {})
        self.api_key = api_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self.base_url = api_cfg.get("base_url", "https://api.deepseek.com/v1")
        self.model = api_cfg.get("model", "deepseek-chat")
        self.fallback_models = api_cfg.get("fallback_models", [])
        ai_cfg = config.get("ai_analysis", {})
        self.batch_size = ai_cfg.get("batch_size", 10)
        self.temperature = ai_cfg.get("temperature", 0.3)
        self.max_tokens = ai_cfg.get("max_tokens_per_item", 800)

    def analyze_batch(self, items, macro_context):
        results = []
        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]
            results.extend(self._analyze_single_batch(batch, macro_context))
        return results
    def _analyze_single_batch(self, items, macro_context):
        items_json = json.dumps([{
            "id": item["id"], "title": item["title"],
            "source": item["source_name"],
            "content": item["content"][:3000],
            "keywords_hit": item.get("keywords_hit", []),
            "entities": item.get("entities", []),
            "base_score": item.get("final_score", 0),
            "published_at": item.get("published_at", "")
        } for item in items], ensure_ascii=False, indent=2)

        user_prompt = f"""\u4eca\u65e5\u60c5\u62a5 {len(items)} \u6761\uff08JSON\uff09\uff1a\n{items_json}\n\n\u8bf7\u5bf9\u6bcf\u6761\u60c5\u62a5\u8fdb\u884c\u56db\u7ef4\u7ed3\u6784\u7814\u5224\uff0c\u8f93\u51fa JSON \u6570\u7ec4\uff0c\u6bcf\u4e2a\u5143\u7d20\u5305\u542b\uff1a\n1. intel_id: \u60c5\u62a5ID\n2. macro_diagnosis: \u56db\u7ef4\u8bca\u65ad accumulation_node/spatial_layer/state_market_shift/class_interest\n3. structural_implication: \u7ed3\u6784\u6027\u542b\u4e49\n4. personal_action_space: window_months/concrete_moves/avoid_traps/signals_to_watch\n5. knowledge_links: \u53cc\u5411\u94fe\u63a5\u6570\u7ec4\n6. confidence: 1-10\u7f6e\u4fe1\u5ea6\n7. contradictions: \u77db\u76fe\u5f85\u9a8c\u8bc1\u70b9\n8. raw_reasoning: \u5b8c\u6574\u63a8\u7406\u94fe\n\u53ea\u8f93\u51fa\u7eafJSON\u6570\u7ec4\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002"""

        system_prompt = self._build_system_prompt(macro_context)
        response = self._call_api(system_prompt, user_prompt)
        return self._parse_response(response, items)

    def _build_system_prompt(self, macro_context):
        return (
            "\u4f60\u662f\u4e3a\u300c\u5904\u4e8e\u7ed3\u6784\u6027\u8f6c\u6298\u671f\u7684\u5e74\u8f7b\u52b3\u52a8\u8005\u300d\u670d\u52a1\u7684\u53c2\u8c0b\u957f\u3002"
            + "\n\u7528\u6237\u753b\u50cf\uff1a" + self.persona + "\n\n"
            + "\u77e5\u8bc6\u5e93\u6838\u5fc3\u5b8f\u89c2\u6982\u5ff5\uff1a" + macro_context + "\n\n"
            + "\u3010\u56db\u7ef4\u5206\u6790\u6846\u67b6\u3011\u5fc5\u987b\u4e25\u683c\u9075\u5b88\n"
            + "1. \u79ef\u7d2f\u5236\u5ea6\u89c6\u89d2: accumulation_node = \u751f\u4ea7/\u5b9e\u73b0/\u5206\u914d/\u518d\u751f\u4ea7\n"
            + "2. \u7a7a\u95f4\u4fee\u6b63\u89c6\u89d2: spatial_layer = \u4e2d\u5fc3/\u5916\u56f4/\u7279\u533a/\u90fd\u5e02\u5708\n"
            + "3. \u56fd\u5bb6-\u5e02\u573a\u8fb9\u754c\u89c6\u89d2: state_market_shift = \u56fd\u5bb6\u8fdb\u573a/\u5e02\u573a\u9000\u573a/\u8fb9\u754c\u6a21\u7cca/\u8bd5\u70b9\u5148\u884c\n"
            + "4. \u9636\u7ea7/\u5229\u76ca\u96c6\u56e2\u89c6\u89d2: class_interest\n\n"
            + "\u8f93\u51fa\u8981\u6c42: \u6bcf\u6761\u60c5\u62a5\u5fc5\u987b\u8f93\u51fa\u5b8c\u65748\u5b57\u6bb5JSON\n"
            + "\u53ea\u8f93\u51fa\u7eafJSON\u6570\u7ec4\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002"
        )

    def _call_api(self, system_prompt, user_prompt):
        models = [{"model": self.model, "base_url": self.base_url, "api_key": self.api_key}] + self.fallback_models
        for i, m in enumerate(models):
            try:
                api_base = m.get("base_url", self.base_url).rstrip("/")
                key = m.get("api_key") or self.api_key
                url = api_base + "/chat/completions"
                payload = json.dumps({
                    "model": m["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": self.temperature,
                    "max_tokens": 8192,
                }).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                    "User-Agent": "opencode/latest/1.3.15/cli",
                    "x-opencode-client": "cli",
                    "x-opencode-session": uuid.uuid4().hex,
                    "x-opencode-project": uuid.uuid4().hex[:8],
                    "x-opencode-request": uuid.uuid4().hex,
                }
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read().decode("utf-8")
                    result = json.loads(raw)
                    msg = result["choices"][0]["message"]
                    content = msg.get("content", "")
                    if not content:
                        for k in ("reasoning_content", "reasoning", "output"):
                            val = msg.get(k, "")
                            if val:
                                content = val
                                break
                    with open(r'C:\Users\admin\Documents\osint\_tmp_raw_resp.txt', 'w', encoding='utf-8') as _f:
                        _f.write(content)
                    if content and content.strip():
                        return content.strip()
                    raise ValueError("empty response")
            except Exception as e:
                print("[AI] model " + m["model"] + " failed: " + str(e))
                if i == len(models) - 1:
                    raise
        return "[]"

    def _parse_response(self, response, original_items):
        response = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", response)
        response = re.sub(r"```json\s*", "", response)
        response = re.sub(r"```\s*$", "", response)
        data = None
        start = response.find("[")
        if start >= 0:
            depth = 0; end = start
            for i in range(start, len(response)):
                if response[i] == "[": depth += 1
                elif response[i] == "]":
                    depth -= 1
                    if depth == 0: end = i; break
            json_str = response[start:end+1]
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                last_brace = json_str.rfind("}")
                if last_brace > 0:
                    truncated = json_str[:last_brace+1]
                    if not truncated.endswith("]"): truncated += "]"
                    try: data = json.loads(truncated)
                    except: pass
        if not data:
            try: data = json.loads(response)
            except: pass
        if not data:
            print("[AI] JSON parse failed, using fallback")
            return [self._fallback_result(item, response) for item in original_items]
        results = []
        for d in data:
            try:
                r = AnalysisResult(
                    intel_id=d.get("intel_id", ""),
                    macro_diagnosis=d.get("macro_diagnosis", {}),
                    structural_implication=d.get("structural_implication", ""),
                    personal_action_space=d.get("personal_action_space", {}),
                    knowledge_links=d.get("knowledge_links", []),
                    confidence=d.get("confidence", 5),
                    contradictions=d.get("contradictions", ""),
                    raw_reasoning=d.get("raw_reasoning", "")
                )
                results.append(r)
            except Exception as e:
                print("[AI] parse item failed: " + str(e))
        while len(results) < len(original_items):
            results.append(self._fallback_result(original_items[len(results)], response))
        return results

    def _fallback_result(self, item, raw):
        return AnalysisResult(
            intel_id=item.get("id", ""),
            macro_diagnosis={"accumulation_node":"\u672a\u8bc6\u522b","spatial_layer":"\u672a\u8bc6\u522b","state_market_shift":"\u672a\u8bc6\u522b","class_interest":"\u672a\u8bc6\u522b"},
            structural_implication="AI \u5206\u6790\u5931\u8d25\uff0c\u9700\u4eba\u5de5\u590d\u6838",
            personal_action_space={"window_months":12,"concrete_moves":[{"action":"\u4eba\u5de5\u590d\u6838\u6b64\u60c5\u62a5","rationale":"AI\u5f02\u5e38","risk":"\u53ef\u80fd\u9057\u6f0f\u5173\u952e\u4fe1\u53f7"}],"avoid_traps":["\u76f2\u76ee\u8ddf\u98ce"],"signals_to_watch":["\u540e\u7eed\u5b98\u65b9\u6587\u4ef6","\u884c\u4e1a\u6570\u636e\u53d8\u5316"]},
            knowledge_links=[], confidence=1,
            contradictions="AI\u5206\u6790\u5f02\u5e38",
            raw_reasoning=raw[:1000]
        )


def analyze_intel(config, persona, knowledge_base, items):
    macro_context = knowledge_base.get_macro_framework_context()
    analyzer = MacroAnalyzer(config, persona, knowledge_base)
    return analyzer.analyze_batch(items, macro_context)


if __name__ == "__main__":
    import sys, argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--intel", required=True)
    parser.add_argument("--persona", default="persona.md")
    parser.add_argument("--vault", default=r"D:\\Codex\u8f93\u51fa\\\u89c6\u9891\u77e5\u8bc6\u5e93")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(args.persona, "r", encoding="utf-8") as f:
        persona = f.read()
    sys.path.insert(0, str(Path(__file__).parent))
    from load_knowledge import load_knowledge
    kb = load_knowledge(args.vault)
    items = []
    with open(args.intel, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line: items.append(json.loads(line))
    print("Analyzing " + str(len(items)) + " items...")
    results = analyze_intel(config, persona, kb, items)
    with open(args.output, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\\n")
    print("Done: " + args.output)
