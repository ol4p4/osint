# -*- coding: utf-8 -*-
r"""question_generator.py - P2b 问题生成器
输入当日 AI 分析摘要，生成 3-5 个开放性问题，引导用户把分析变成自己的判断。
用法：
  python question_generator.py --analysis-file <analysis_YYYYMMDD.jsonl 的摘要文本文件>
  或 from question_generator import QuestionGenerator; QuestionGenerator(config).generate_daily_questions(text)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# AI 失败时的兜底模板问题
FALLBACK_QUESTIONS = [
    {"name": "反方", "question": "今天的分析里，哪个结论你最不认同？支撑你反对的证据是什么？"},
    {"name": "因果", "question": "这些现象背后，哪一个是最根本的驱动因素？为什么是它而不是别的？"},
    {"name": "反事实", "question": "如果这个判断错了，最可能是因为忽略了什么变量？"},
    {"name": "个人", "question": "这个判断如果成立，你未来 6 个月应该做什么准备？"},
    {"name": "空白", "question": "今天的情报里，哪个重要问题没人讨论？你能提出一个更好的问法吗？"},
]


class QuestionGenerator:
    def __init__(self, config):
        self.config = config or {}
        self._analyzer = None

    def _ai(self):
        """延迟初始化 AI 调用器，复用 analyze.MacroAnalyzer._call_api"""
        if self._analyzer is None:
            sys.path.insert(0, str(Path(__file__).parent))
            from analyze import MacroAnalyzer
            self._analyzer = MacroAnalyzer(self.config, "", None)
        return self._analyzer

    def generate_daily_questions(self, analysis_text, n=5):
        """输入当日分析摘要文本，返回 [{name, question}] 列表"""
        analysis_text = (analysis_text or "").strip()
        if not analysis_text:
            print("[QGEN] empty analysis text, use fallback questions")
            return FALLBACK_QUESTIONS[:n]
        system = ("你是批判性思维教练。基于当日情报分析，生成开放性问题帮助用户形成独立判断。"
                  "问题要具体、可辩论、指向用户自己的证据和逻辑，不要是能直接搜到答案的问题。")
        prompt = (f"今日分析摘要：\n{analysis_text[:3000]}\n\n"
                  f"生成 {n} 个开放性问题，严格只输出 JSON 数组，不要 markdown："
                  '[{"name":"问题类型短词","question":"问题文本"}]')
        try:
            resp = self._ai()._call_api(system, prompt)
            questions = self._parse_json_array(resp)
        except Exception as e:
            print(f"[QGEN] AI call failed ({e}), use fallback")
            questions = []
        if not questions:
            questions = FALLBACK_QUESTIONS[:n]
        return questions[:n]

    def _parse_json_array(self, text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start = text.find("[")
        if start < 0:
            return []
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        out = []
                        for q in json.loads(text[start:i + 1]):
                            if isinstance(q, dict) and q.get("question"):
                                out.append({"name": q.get("name", "问题"), "question": q["question"]})
                        return out
                    except Exception:
                        return []
        return []

    def save_questions(self, questions, output_dir):
        """把问题写进产物目录 questions/question_YYYYMMDD.md"""
        d = Path(output_dir) / "questions"
        d.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"# 每日开放性问题 - {date_str}", ""]
        for i, q in enumerate(questions, 1):
            lines.append(f"## Q{i} · {q.get('name', '问题')}")
            lines.append(q["question"])
            lines.append("")
        p = d / f"question_{date_str}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        print(f"[QGEN] saved: {p}")
        return p


def main():
    import argparse
    import yaml
    parser = argparse.ArgumentParser(description="P2b 问题生成器")
    parser.add_argument("--analysis-file", metavar="PATH", help="当日分析摘要文本文件")
    parser.add_argument("--analysis-text", metavar="TEXT", help="直接传分析文本")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()

    cfg_path = Path(__file__).parent.parent / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    text = args.analysis_text or ""
    if args.analysis_file:
        text = Path(args.analysis_file).read_text(encoding="utf-8")
    gen = QuestionGenerator(config)
    questions = gen.generate_daily_questions(text, n=args.n)
    output_dir = (config.get("paths", {}) or {}).get("output_dir", r"D:\Codex输出\osint_卫星图")
    gen.save_questions(questions, output_dir)
    for i, q in enumerate(questions, 1):
        print(f"  Q{i} [{q['name']}] {q['question']}")


if __name__ == "__main__":
    main()
