# -*- coding: utf-8 -*-
r"""worldview_engine.py - 三观输入引擎（P2c：对话式引导录入）
流程：
  --seed         AI 从 persona.md + views.yaml 起草三观初稿（draft: true，功能即刻可用）
  --interactive  3 阶段 × 3 问 = 9 轮引导对话（世界观→人生观→价值观）→ AI 浓缩 → 覆写
                 worldview.yaml（draft: false）
  --show         打印当前三观档案与注入预览（透传 worldview_loader）

落盘：
  worldview.yaml（仓库根，唯一事实源，全链路自动生效）
  data/dialogues/worldviews/worldview_*.json（对话归档）
  视频知识库 wiki/views/worldview.md（Obsidian 镜像，单页覆盖）+ index/log 同步

护栏：AI 合成失败时不动 worldview.yaml（宁可没有，不可写坏）。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

WORLDVIEW_FILE = PROJECT / "worldview.yaml"

STAGES = [
    {"name": "世界观", "questions": [
        "你认为这个世界/社会运行的核心规律是什么？（比如：什么决定资源流向、什么推动周期）",
        "判断未来趋势时，你最先看什么力量？（制度/资本/技术/人口/地缘…怎么排序）",
        "对中国未来 5-10 年的结构性走向，你的核心判断是什么？",
    ]},
    {"name": "人生观", "questions": [
        "什么是你想要的『好生活』？描述一下它的样子。",
        "个人努力和结构的力量，边界在哪里？哪些事上个人能改变，哪些只能顺应？",
        "面对不确定性，你的策略是什么？（求稳/对冲/下注，为什么）",
    ]},
    {"name": "价值观", "questions": [
        "对你来说什么最重要？请给出优先级排序。",
        "什么事是你绝对不做的？（红线）",
        "你最反感参谋给你哪类建议？（比如：正确的废话/投机建议/鸡汤…）",
    ]},
]

WORLDVIEW_SCHEMA_HINT = (
    "严格只输出 JSON（不要 markdown）："
    '{"worldview":{"summary":"≤40字","propositions":["命题1","命题2","命题3"]},'
    '"lifeview":{"summary":"≤40字","propositions":["命题1","命题2","命题3"]},'
    '"values":{"summary":"≤30字","priorities":["最重要→次之→…(3-4项)"],'
    '"red_lines":["红线1","红线2"]},'
    '"analysis_directives":["给参谋长的研判指令1（优先从…角度/警惕…类建议）","指令2"]}'
)


class WorldviewEngine:
    def __init__(self, config):
        self.config = config or {}
        paths = self.config.get("paths", {})
        self.output_dir = Path(paths.get("output_dir", r"D:\osint\data"))
        self.archive_dir = self.output_dir / "dialogues" / "worldviews"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._analyzer = None

    # ---------- AI ----------
    def _ai(self):
        """延迟初始化，复用 analyze.MacroAnalyzer._call_api（参谋长模型）"""
        if self._analyzer is None:
            from analyze import MacroAnalyzer
            self._analyzer = MacroAnalyzer(self.config, "", None)
        return self._analyzer

    def _ai_call(self, system, prompt, fallback=""):
        try:
            resp = self._ai()._call_api(system, prompt)
            resp = (resp or "").strip().strip('"')
            return resp if resp else fallback
        except Exception as e:
            print(f"[WORLDVIEW] AI call failed ({e})")
            return fallback

    def _parse_json(self, text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return None
        return None

    # ---------- 引导对话 ----------
    def _deepen_question(self, question, transcript):
        """结合已有回答深化追问（同 dialogue_engine 机制）"""
        if not transcript:
            return question
        history = "\n".join(f"[{t['role']}] {t['text']}" for t in transcript[-6:])
        system = "你是个追根问底的参谋。基于对话历史，把固定追问深化得更具体、更贴身，只输出问题本身，不超过60字。"
        prompt = f"对话历史：\n{history}\n\n原始追问：{question}\n输出深化后的追问。"
        return self._ai_call(system, prompt, question)

    def run_interactive(self, answers=None):
        """9 轮引导对话 → AI 浓缩 → 覆写 worldview.yaml（draft: false）。
        answers 传预置答案列表时走非交互模式（测试用），None 时终端交互。"""
        transcript = []
        n = 0
        for stage in STAGES:
            print(f"\n=== {stage['name']} ===")
            for q in stage["questions"]:
                n += 1
                qq = self._deepen_question(q, transcript)
                if answers is not None:
                    ans = answers.pop(0) if answers else "（未回答）"
                else:
                    print(f"\n【{n}/9 · {stage['name']}】{qq}")
                    ans = input("> ").strip() or "（未回答）"
                transcript.append({"role": "ai_question", "text": qq})
                transcript.append({"role": "user", "text": ans})
        wv = self._synthesize(transcript, draft=False)
        if wv:
            self._save(wv, transcript, source="interactive")
        else:
            print("[WORLDVIEW] AI 合成失败，worldview.yaml 未改动（对话已归档可重试）")
            self._archive(transcript, source="interactive_failed")
        return wv

    def run_seed(self):
        """AI 从 persona + views 起草初稿（draft: true）"""
        persona = ""
        pf = PROJECT / "persona.md"
        if pf.exists():
            persona = pf.read_text(encoding="utf-8")[:2500]
        views_text = ""
        vf = PROJECT / "views.yaml"
        if vf.exists():
            views_text = vf.read_text(encoding="utf-8")[:1500]
        system = ("你是用户的参谋长。根据用户的画像与既有观点材料，起草 TA 的三观结构化初稿"
                  "（世界观=世界如何运行；人生观=人该怎么活；价值观=什么值得/不可妥协）。"
                  "语言务实具体，每条命题必须可被日常新闻检验；不要空话套话。严格输出 JSON，不要 markdown。")
        prompt = (f"用户画像：\n{persona}\n\n既有观点材料：\n{views_text}\n\n{WORLDVIEW_SCHEMA_HINT}")
        wv = self._parse_json(self._ai_call(system, prompt))
        if not isinstance(wv, dict) or not isinstance(wv.get("worldview"), dict):
            print("[WORLDVIEW] seed 失败：AI 未返回有效 JSON（worldview.yaml 未改动）")
            return None
        wv["draft"] = True
        self._save(wv, [{"role": "system", "text": "seed from persona.md + views.yaml"}], source="seed")
        return wv

    # ---------- 合成与落盘 ----------
    def _synthesize(self, transcript, draft):
        history = "\n".join(f"[{t['role']}] {t['text']}" for t in transcript)
        system = ("你是用户的参谋长。把用户关于三观的对话浓缩为结构化三观档案："
                  "提炼 TA 的真实立场，不要替 TA 发明立场；语言务实具体。严格输出 JSON，不要 markdown。")
        resp = self._ai_call(system, history + "\n\n" + WORLDVIEW_SCHEMA_HINT)
        wv = self._parse_json(resp)
        if not isinstance(wv, dict) or not isinstance(wv.get("worldview"), dict):
            return None
        wv["draft"] = draft
        return wv

    def _save(self, wv, transcript, source):
        wv["version"] = int(wv.get("version") or 1)
        wv["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        wv["source"] = source
        import yaml
        header = ("# 三观档案 - 参谋系统的个人视角锚点（世界观/人生观/价值观）\n"
                  "# 唯一事实源：修改后全链路（本地分析/每小时研判/问答/CI）自动生效\n"
                  "# 重新录入: python local/worldview_engine.py --interactive\n")
        WORLDVIEW_FILE.write_text(
            header + yaml.safe_dump(wv, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        self._archive(transcript, source)
        self._mirror_kb(wv)
        print(f"[WORLDVIEW] saved: {WORLDVIEW_FILE} (draft={wv.get('draft')})")

    def _archive(self, transcript, source):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        f = self.archive_dir / f"worldview_{ts}.json"
        f.write_text(json.dumps({"source": source, "transcript": transcript},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[WORLDVIEW] transcript: {f}")

    def _mirror_kb(self, wv):
        """Obsidian 镜像页（单页覆盖）+ index/log 同步；失败不影响主流程"""
        try:
            from kb_linker import DEFAULT_VAULT, _append_log, _insert_index_link
            from worldview_loader import build_worldview_prompt
            vault = Path(DEFAULT_VAULT)
            page = vault / "wiki" / "views" / "worldview.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            import yaml
            page.write_text(
                "---\ntype: worldview\nupdated: " + str(wv.get("updated", "-")) + "\n---\n\n"
                "# 我的三观\n\n"
                "> 参谋系统研判的个人视角锚点。唯一事实源：`D:\\osint\\worldview.yaml`"
                "（修改后本地分析/每小时研判/问答/CI 全链路自动生效）\n\n"
                "## 注入视角（研判 prompt 实际收到的文本）\n\n"
                + build_worldview_prompt() + "\n\n"
                "## 完整档案\n\n```yaml\n"
                + yaml.safe_dump(wv, allow_unicode=True, sort_keys=False).strip() + "\n```\n\n"
                "## 关联\n- [[参谋系统假设树]]\n",
                encoding="utf-8")
            _insert_index_link(vault / "wiki" / "index.md", "worldview", "## Views")
            _append_log(vault / "wiki" / "log.md", "三观档案更新",
                        f"[[worldview]] draft={wv.get('draft')} source={wv.get('source')}")
            print(f"[WORLDVIEW] kb mirror: {page}")
        except Exception as e:
            print(f"[WORLDVIEW] kb mirror failed: {e}")


def main():
    import yaml
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))

    ap = argparse.ArgumentParser(description="三观输入引擎：--seed 起草初稿 / --interactive 引导录入 / --show 查看")
    ap.add_argument("--seed", action="store_true", help="AI 从 persona+views 起草初稿（draft: true）")
    ap.add_argument("--interactive", action="store_true", help="9 轮引导对话（覆盖写，draft: false）")
    ap.add_argument("--show", action="store_true", help="查看当前档案与注入预览")
    ap.add_argument("--test-answers", metavar="JSON", help=argparse.SUPPRESS)
    args = ap.parse_args()

    eng = WorldviewEngine(config)
    if args.seed:
        eng.run_seed()
        return 0
    if args.interactive:
        answers = json.loads(args.test_answers) if args.test_answers else None
        eng.run_interactive(answers=answers)
        return 0
    if args.show:
        from worldview_loader import WORLDVIEW_FILE as WF, build_worldview_prompt, load_worldview
        wv = load_worldview()
        if not wv:
            print(f"[WORLDVIEW] 无三观档案: {WF}")
            print("[WORLDVIEW] 录入: python local/worldview_engine.py --interactive （或 --seed）")
            return 1
        print("[WORLDVIEW] === 当前档案 ===")
        print(yaml.safe_dump(wv, allow_unicode=True, sort_keys=False).strip())
        print("\n[WORLDVIEW] === 注入预览 ===")
        print(build_worldview_prompt())
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
